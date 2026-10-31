"""Local RSyslog server storage and ingestion figures; no remote resource estimates."""
from pathlib import Path
import shutil
import time
from config import config
import database

STARTED_AT = time.monotonic()


def collect_system_metrics():
    path = Path(config.DATABASE_PATH).resolve()
    storage = shutil.disk_usage(path.parent)
    files = {name: Path(str(path) + suffix) for name, suffix in (('database', ''), ('wal','-wal'), ('shared_memory','-shm'))}
    sizes = {}
    for name, file in files.items():
        try:
            sizes[name] = file.stat().st_size
        except FileNotFoundError:
            sizes[name] = 0
    hosts = database.get_db_connection().execute("""SELECT COUNT(*) AS known,
        COALESCE(SUM(recent),0) AS active FROM (
            SELECT host, MAX(julianday(COALESCE(NULLIF(received_at,''),timestamp))) >= julianday('now','-15 minutes') AS recent
            FROM logs WHERE app <> 'host-monitor' GROUP BY host
            UNION ALL SELECT host, 0 FROM monitored_hosts WHERE host NOT IN (SELECT host FROM logs WHERE app <> 'host-monitor')
        )""").fetchone()
    heartbeats = database.get_monitored_hosts()
    from ingestion_counters import snapshot
    from storage_policy import status
    return dict(database.get_ingestion_metrics(),
        discarded=snapshot(), storage_policy=status(),
        known_hosts=hosts['known'], active_hosts_15m=hosts['active'],
        heartbeat_hosts=len(heartbeats), offline_hosts=sum(time.time()-host['last_seen']>300 for host in heartbeats),
        storage=dict(total_bytes=storage.total, used_bytes=storage.used, free_bytes=storage.free,
                     used_percent=round(100*storage.used/storage.total, 1) if storage.total else 0),
        database=dict(path=str(path), **{f'{name}_bytes': size for name,size in sizes.items()}, total_bytes=sum(sizes.values())),
        retention_days=database.get_all_settings()['retention_days'],
        uptime_seconds=round(time.monotonic()-STARTED_AT), measured_at=time.time())
