"""Bound active SQLite data, reuse freed pages, and warn before storage exhaustion."""
import shutil
import time
import threading

_pressure = threading.Event()
from pathlib import Path
from config import config


def usage(conn):
    page_size = conn.execute('PRAGMA page_size').fetchone()[0]
    pages = conn.execute('PRAGMA page_count').fetchone()[0]
    free = conn.execute('PRAGMA freelist_count').fetchone()[0]
    return {'used_bytes': max(0, pages-free)*page_size, 'allocated_bytes': pages*page_size,
            'reusable_bytes': free*page_size}


def limit_bytes(conn):
    row = conn.execute("SELECT value FROM settings WHERE key='max_database_mb'").fetchone()
    try:
        return max(0, int(row[0])) * 1024 * 1024 if row else 0
    except (ValueError, TypeError):
        return 0


def can_accept(conn, estimated_bytes):
    try:
        db_dir = Path(config.DATABASE_PATH).resolve().parent
        disk = shutil.disk_usage(db_dir)
        if disk.free < 250 * 1024 * 1024 or (disk.total > 0 and (disk.free / disk.total) < 0.02):
            _pressure.set()
            return False
    except Exception:
        pass
    limit = limit_bytes(conn)
    if not limit:
        return True
    # Include room for indexes, encoding and the transaction itself.
    needed = estimated_bytes * 3 + 65536
    accepted = usage(conn)['used_bytes'] + needed < limit
    if not accepted and needed < limit:
        _pressure.set()
    return accepted


def trim_database():
    from database import get_db_connection
    conn = get_db_connection()
    limit = limit_bytes(conn)
    deleted = 0
    deadline = time.monotonic() + 2
    target = limit * (.8 if _pressure.is_set() else .9)
    _pressure.clear()
    disk_tight = False
    try:
        db_dir = Path(config.DATABASE_PATH).resolve().parent
        disk = shutil.disk_usage(db_dir)
        if disk.free < 1024 * 1024 * 1024 or (disk.total > 0 and (disk.free / disk.total) < 0.05):
            disk_tight = True
    except Exception:
        pass

    while (limit and usage(conn)['used_bytes'] > target) or (disk_tight and deleted < 5000):
        if time.monotonic() >= deadline:
            break
        with conn:
            # Count at most 20k index entries, not the entire history. Keep
            # small databases conservative so one batch does not erase all logs.
            available = conn.execute('SELECT COUNT(*) FROM (SELECT id FROM logs LIMIT 20000)').fetchone()[0]
            if not available:
                break
            batch = min(1000, max(1, available // 20))
            result = conn.execute('DELETE FROM logs WHERE id IN (SELECT id FROM logs ORDER BY id LIMIT ?)', (batch,))
        deleted += result.rowcount
        if not result.rowcount:
            break
    if deleted:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='incidents'").fetchone():
            with conn:
                conn.execute("DELETE FROM incidents WHERE julianday(last_seen) < COALESCE((SELECT MIN(julianday(COALESCE(NULLIF(received_at,''),timestamp))) FROM logs),julianday('now'))")
        conn.execute('PRAGMA wal_checkpoint(PASSIVE)')
        from metrics_cache import invalidate
        invalidate()
    return deleted


def status():
    from database import get_db_connection
    conn = get_db_connection()
    size = usage(conn)
    limit = limit_bytes(conn)
    disk = shutil.disk_usage(Path(config.DATABASE_PATH).resolve().parent)
    disk_percent = 100 * disk.used / disk.total if disk.total else 0
    disk_free_mb = disk.free / (1024 * 1024)
    warnings = []
    if disk_percent >= 90 or disk_free_mb < 500:
        warnings.append(f'Dateisystem zu {disk_percent:.0f} % belegt ({disk_free_mb:.0f} MB frei). Speicherplatz freigeben.')
    if limit and size['used_bytes'] >= limit * .8:
        warnings.append('Datenbank nahe der eingestellten Grenze. Älteste Logs werden bei Bedarf automatisch bereinigt.')
    return dict(size, limit_bytes=limit, disk_free_mb=round(disk_free_mb, 1), disk_total_mb=round(disk.total / (1024 * 1024), 1), warning=bool(warnings), message=' '.join(warnings))
