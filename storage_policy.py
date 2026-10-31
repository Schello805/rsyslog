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
    while limit and usage(conn)['used_bytes'] > target and time.monotonic() < deadline:
        with conn:
            total = conn.execute('SELECT COUNT(*) FROM logs').fetchone()[0]
            batch = min(1000, max(1, total // 20))
            result = conn.execute('DELETE FROM logs WHERE id IN (SELECT id FROM logs ORDER BY id LIMIT ?)', (batch,))
        deleted += result.rowcount
        if not result.rowcount:
            break
    if deleted:
        conn.execute('PRAGMA wal_checkpoint(PASSIVE)')
    return deleted


def status():
    from database import get_db_connection
    conn = get_db_connection()
    size = usage(conn)
    limit = limit_bytes(conn)
    disk = shutil.disk_usage(Path(config.DATABASE_PATH).resolve().parent)
    disk_percent = 100 * disk.used / disk.total if disk.total else 0
    warnings = []
    if disk_percent >= 90:
        warnings.append(f'Dateisystem zu {disk_percent:.0f} % belegt. Speicherplatz freigeben.')
    if limit and size['used_bytes'] >= limit * .8:
        warnings.append('Datenbank nahe der eingestellten Grenze. Älteste Logs werden bei Bedarf automatisch bereinigt.')
    return dict(size, limit_bytes=limit, warning=bool(warnings), message=' '.join(warnings))
