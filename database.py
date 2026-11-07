"""
SQLite Database Layer for RSyslog Web.
Provides thread-safe connections, WAL mode for high performance,
indices for filtering/search, and log query aggregation.
"""

import json
import re
import sqlite3
import threading
from contextlib import nullcontext
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from config import config

_local = threading.local()
# Keep the expression identical in queries and index, including legacy rows.
RECEIPT_SQL = "julianday(COALESCE(NULLIF(received_at,''),timestamp))"


def get_db_connection() -> sqlite3.Connection:
    """Returns a thread-local SQLite connection with dictionary row access."""
    if not hasattr(_local, "connection") or _local.connection is None:
        conn = sqlite3.connect(
            config.DATABASE_PATH,
            check_same_thread=False,
            timeout=30.0,
        )
        conn.row_factory = sqlite3.Row
        # Enable WAL (Write-Ahead Logging) mode and fast synchronous settings
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        _local.connection = conn
    return _local.connection


def init_db() -> None:
    """Creates tables and indices if they do not exist."""
    conn = get_db_connection()
    with conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                host TEXT NOT NULL,
                app TEXT NOT NULL,
                level TEXT NOT NULL,
                facility TEXT,
                message TEXT NOT NULL,
                raw TEXT
            )
            """
        )
        columns = {row['name'] for row in conn.execute('PRAGMA table_info(logs)')}
        if 'metadata' not in columns:
            conn.execute("ALTER TABLE logs ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'")
        if 'received_at' not in columns:
            conn.execute("ALTER TABLE logs ADD COLUMN received_at TEXT NOT NULL DEFAULT ''")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_received ON logs(received_at)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_logs_receipt_time ON logs({RECEIPT_SQL})")
        # Performance indexes for dashboard filtering and sorting
        conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON logs(timestamp DESC)")
        # Do not build additional full-history sorting indexes during startup:
        # this blocks service readiness on large existing databases.
        conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_level ON logs(level)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_host ON logs(host)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_app ON logs(app)")

        conn.execute("CREATE TABLE IF NOT EXISTS resource_samples(host TEXT PRIMARY KEY, sampled_at REAL NOT NULL, payload TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS monitored_hosts (host TEXT PRIMARY KEY, last_seen REAL NOT NULL, offline INTEGER NOT NULL DEFAULT 0)")

        conn.execute("CREATE TABLE IF NOT EXISTS host_aliases (host TEXT PRIMARY KEY, name TEXT NOT NULL)")

        conn.execute("CREATE TABLE IF NOT EXISTS rule_notifications (rule_id TEXT, host TEXT, app TEXT, channel TEXT, last_sent REAL NOT NULL, PRIMARY KEY(rule_id,host,app,channel))")

        # Settings Key-Value Table
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )

    with conn:
        conn.execute('CREATE TABLE IF NOT EXISTS source_dns (ip TEXT PRIMARY KEY, name TEXT NOT NULL)')
    normalize_recorded_sources()
    from incidents import initialize
    initialize(conn)
    if not conn.execute("SELECT 1 FROM settings WHERE key='migration_quiet_availability_v1'").fetchone():
        with conn:
            messages = ('ALARM: Seit 5 Minuten kein Heartbeat', 'ENTWARNUNG: Heartbeat wieder empfangen')
            conn.execute("UPDATE logs SET level='INFO' WHERE app='host-monitor' AND message IN (?,?) AND level!='INFO'", messages)
            conn.execute("DELETE FROM incidents WHERE app='host-monitor' AND message IN (?,?)", messages)
            conn.execute("INSERT INTO settings(key,value) VALUES('migration_quiet_availability_v1','done')")
    from analysis_cache import initialize as initialize_cache
    initialize_cache(conn)


def remember_source_dns(ip, name):
    conn = get_db_connection()
    row = conn.execute('SELECT name FROM source_dns WHERE ip=?', (ip,)).fetchone()
    if not row or row['name'] != name:
        with conn:
            conn.execute('INSERT INTO source_dns VALUES(?,?) ON CONFLICT(ip) DO UPDATE SET name=excluded.name', (ip, name))
        return True
    return False


def normalize_recorded_sources():
    """Only recorded source IPs may change historical identity, never names/PTR."""
    from ipaddress import ip_address
    conn = get_db_connection()
    marker = 'migration_transport_source_v2'
    if get_setting(marker):
        return
    last_id = 0
    while True:
        rows = conn.execute('SELECT id,host,metadata FROM logs WHERE id>? ORDER BY id LIMIT 500', (last_id,)).fetchall()
        if not rows:
            break
        with conn:
            for row in rows:
                last_id = row['id']
                try:
                    metadata = json.loads(row['metadata'] or '{}')
                    address = str(ip_address(metadata['source_ip']))
                except (ValueError, TypeError, KeyError):
                    continue
                if row['host'] != address:
                    metadata.setdefault('reported_host', row['host'])
                    conn.execute('UPDATE logs SET host=?,metadata=? WHERE id=?', (address,json.dumps(metadata),row['id']))
    set_setting(marker, 'done')


def get_setting(key: str, default: Any = None) -> Any:
    """Retrieves a single setting from the database or returns default."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    if row:
        return row["value"]
    return default


def set_setting(key: str, value: Any) -> None:
    """Saves a setting value in the database."""
    conn = get_db_connection()
    with conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )


def get_all_settings() -> Dict[str, Any]:
    """Retrieves all settings, falling back to .env / config defaults."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT key, value FROM settings")
    db_settings = {row["key"]: row["value"] for row in cursor.fetchall()}

    # Helper for boolean conversion
    def as_bool(val, default_val):
        if val is None:
            return default_val
        return str(val).lower() in ("true", "1", "yes", "on")

    # Helper for int conversion
    def as_int(val, default_val):
        try:
            return int(val)
        except (ValueError, TypeError):
            return default_val

    telegram_token = db_settings.get("telegram_bot_token", config.TELEGRAM_BOT_TOKEN)
    telegram_chat = db_settings.get("telegram_chat_id", config.TELEGRAM_CHAT_ID)
    ntfy_topic = db_settings.get("ntfy_topic", config.NTFY_TOPIC)
    ntfy_server = db_settings.get("ntfy_server", config.NTFY_SERVER or "https://ntfy.sh")

    try:
        rules = json.loads(db_settings.get('alert_rules', '[]'))
        from custom_alerts import validate_rules
        rules = validate_rules(rules)
    except (ValueError, TypeError):
        rules = []

    return {
        'alert_rules': rules,
        # Telegram
        "telegram_enabled": as_bool(db_settings.get("telegram_enabled"), bool(telegram_token and telegram_chat)),
        "telegram_bot_token": telegram_token or "",
        "telegram_chat_id": telegram_chat or "",
        # Ntfy
        "ntfy_enabled": as_bool(db_settings.get("ntfy_enabled"), bool(ntfy_topic)),
        "ntfy_topic": ntfy_topic or "",
        "ntfy_server": ntfy_server or "https://ntfy.sh",
        # Thresholds & Rules
        "alert_min_level": db_settings.get("alert_min_level", config.ALERT_MIN_LEVEL or "ERROR").upper(),
        "alert_threshold_count": as_int(db_settings.get("alert_threshold_count"), 1),
        "alert_threshold_window_minutes": as_int(db_settings.get("alert_threshold_window_minutes"), 5),
        "alert_throttle_seconds": as_int(db_settings.get("alert_throttle_seconds"), config.ALERT_THROTTLE_SECONDS or 30),
        "alert_ignore_hosts": db_settings.get("alert_ignore_hosts", ""),
        "dns_servers": db_settings.get("dns_servers", ""),
        "dns_search_domain": db_settings.get("dns_search_domain", ""),
        # Retention
        "retention_days": as_int(db_settings.get("retention_days"), 0),
        "discard_debug": as_bool(db_settings.get("discard_debug"), True),
        "max_database_mb": max(0, as_int(db_settings.get("max_database_mb"), 0)),
    }


def save_all_settings(settings: Dict[str, Any]) -> None:
    """Persists a dictionary of settings into the SQLite database."""
    if 'max_database_mb' in settings:
        value = settings['max_database_mb']
        if isinstance(value, bool) or not str(value).isdigit() or (int(value) != 0 and not 16 <= int(value) <= 1048576):
            raise ValueError('Datenbankgrenze: 0 (unbegrenzt) oder 16 bis 1048576 MB.')
    if 'dns_servers' in settings:
        from ipaddress import ip_address
        servers = [value.strip() for value in str(settings['dns_servers']).split(',') if value.strip()]
        if len(servers) > 3:
            raise ValueError('Höchstens drei DNS-Server als IP-Adressen eingeben.')
        try:
            settings['dns_servers'] = ', '.join(str(ip_address(value)) for value in servers)
        except ValueError:
            raise ValueError('DNS-Server müssen gültige IP-Adressen sein.') from None
    if 'dns_search_domain' in settings:
        from host_resolution import clean_hostname
        domain = str(settings['dns_search_domain']).strip().rstrip('.')
        if domain and not clean_hostname(domain):
            raise ValueError('Bitte eine gültige lokale DNS-Domain eingeben.')
        settings['dns_search_domain'] = domain
    conn = get_db_connection()
    with conn:
        for k, v in settings.items():
            if k == 'alert_rules':
                from custom_alerts import validate_rules
                rules = validate_rules(v)
                v = json.dumps(rules, ensure_ascii=False)
                if rules:
                    conn.execute('DELETE FROM rule_notifications WHERE rule_id NOT IN (' + ','.join('?' for _ in rules) + ')', [rule['id'] for rule in rules])
                else:
                    conn.execute('DELETE FROM rule_notifications')
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(k), str(v)),
            )

    if {'dns_servers','dns_search_domain'} & settings.keys():
        from host_resolution import reset_dns_cache
        reset_dns_cache()


def delete_logs_older_than(days: int) -> int:
    """Deletes logs older than a given number of days. If days <= 0, does nothing."""
    if days <= 0:
        return 0
    from datetime import datetime, timezone, timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    conn = get_db_connection()
    count = 0
    # Short commits release SQLite's writer lock between batches. The existing
    # receipt-time expression index also avoids repeated full-history scans.
    while True:
        with conn:
            deleted = conn.execute(f"DELETE FROM logs WHERE id IN (SELECT id FROM logs WHERE {RECEIPT_SQL} < julianday(?) LIMIT 1000)", (cutoff,)).rowcount
        count += deleted
        if deleted < 1000:
            break
    with conn:
        conn.execute("DELETE FROM incidents WHERE julianday(last_seen) < julianday(?)", (cutoff,))
    if count:
        from metrics_cache import invalidate
        invalidate()
        conn.execute('PRAGMA wal_checkpoint(PASSIVE)')
    return count


def insert_log(
    host: str,
    app: str,
    level: str,
    message: str,
    facility: Optional[str] = "user",
    raw: Optional[str] = None,
    timestamp: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    apply_ingestion_rules: bool = False,
    _batch: bool = False,
) -> Optional[Dict[str, Any]]:
    """Inserts a new log entry and returns the inserted record as a dict."""
    if not timestamp:
        timestamp = datetime.now(timezone.utc).isoformat()

    # Structured HTTP clients may place a complete CEF packet in message as well.
    if isinstance(message, str) and message.lstrip('\ufeff').startswith(('CEF:0|', 'CEF:1|')):
        from syslog_parser import parse_syslog_message
        parsed = parse_syslog_message(message, client_ip=host)
        if parsed.get('metadata'):
            raw = raw or parsed['raw']
            host, app, level, message = parsed['host'], parsed['app'], parsed['level'], parsed['message']
            metadata = dict(parsed['metadata'], **(metadata or {}))
    if metadata and metadata.get('source_ip'):
        from ipaddress import ip_address
        try:
            host = str(ip_address(metadata['source_ip']))
        except ValueError:
            pass
    if apply_ingestion_rules:
        from availability_policy import is_availability_notice
        if is_availability_notice(app, message, metadata):
            return None
    if apply_ingestion_rules and app == 'resource-monitor' and str(message).startswith('METRICS '):
        from resource_metrics import store
        conn = get_db_connection()
        with (nullcontext() if _batch else conn):
            store(conn, host, message)
        return None  # Samples are state, not log events or alert candidates.
    normalized_level = level.upper().strip()
    if apply_ingestion_rules:
        from message_severity import effective_level
        corrected_level = effective_level(normalized_level, message, metadata)
        if corrected_level != normalized_level:
            metadata = dict(metadata or {})
            metadata['transport_level'] = normalized_level
            metadata['severity_source'] = 'application_prefix'
            normalized_level = corrected_level
    if (apply_ingestion_rules and normalized_level == 'DEBUG'
            and str(get_setting('discard_debug', 'true')).lower() in ('true', '1', 'yes', 'on')):
        from ingestion_counters import discarded
        discarded('debug')
        return None
    clean_host = (host or "unknown").strip()
    clean_app = (app or "syslog").strip()
    clean_msg = (message or "").strip()

    conn = get_db_connection()
    if apply_ingestion_rules:
        from storage_policy import can_accept
        if not can_accept(conn, len((raw or clean_msg).encode('utf-8')) + len(clean_msg.encode('utf-8'))):
            from ingestion_counters import discarded
            discarded('storage')
            return None
    try:
        with (nullcontext() if _batch else conn):
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO logs (timestamp, host, app, level, facility, message, raw, metadata, received_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    timestamp,
                    clean_host,
                    clean_app,
                    normalized_level,
                    facility or "user",
                    clean_msg,
                    raw or clean_msg,
                    json.dumps(metadata or {}, ensure_ascii=False),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            log_id = cursor.lastrowid
            from incidents import record
            record(conn, dict(host=clean_host,app=clean_app,level=normalized_level,message=clean_msg,
                              timestamp=timestamp,received_at=datetime.now(timezone.utc).isoformat()))
    except sqlite3.OperationalError as exc:
        if not _batch and apply_ingestion_rules and getattr(exc, 'sqlite_errorcode', None) == sqlite3.SQLITE_FULL:
            from ingestion_counters import discarded
            discarded('storage')
            return None
        raise

    return {
        "id": log_id,
        "timestamp": timestamp,
        "host": clean_host,
        "display_host": clean_host if _batch else get_host_display_name(clean_host),
        "app": clean_app,
        "level": normalized_level,
        "facility": facility or "user",
        "message": clean_msg,
        "raw": raw or clean_msg,
        "metadata": metadata or {},
    }


def insert_logs(entries):
    """Commit a bounded batch before returning any successful records."""
    conn = get_db_connection()
    results = []
    try:
        with conn:
            for entry in entries:
                results.append(insert_log(**entry, _batch=True))
    except Exception:
        # The enclosing transaction rolled back. Retry independently so one bad
        # packet/full disk cannot lose other accepted entries or leak phantom IDs.
        retry = []
        for index, entry in enumerate(entries):
            if index < len(results) and results[index] is None:
                retry.append(None)  # Already rejected by ingestion policy.
                continue
            try:
                retry.append(insert_log(**entry))
            except Exception as exc:
                retry.append(exc)
        return retry
    # UI name lookups may wait on the metrics cache. Never do that while the
    # outer batch still owns SQLite's single writer lock, and never retry an
    # already committed batch if enrichment fails.
    try:
        aliases = get_host_aliases() if any(result is not None for result in results) else {}
        for result in results:
            if result is not None:
                result['display_host'] = aliases.get(result['host'], result['host'])
    except Exception:
        pass  # Stored records already have a safe source-IP display fallback.
    return results


def get_logs(
    page: int = 1,
    per_page: int = 50,
    level: Optional[str] = None,
    host: Optional[str] = None,
    app: Optional[str] = None,
    search: Optional[str] = None,
    hours: Optional[int] = None,
    sort_by: str = "timestamp",
    sort_order: str = "desc",
    exclude_hosts: Optional[List[str]] = None,
    warning_only: bool = False,
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Retrieves filtered logs with pagination.
    Returns (records, total_matching_records).
    """
    conn = get_db_connection()
    conditions = []
    params: List[Any] = []

    if warning_only:
        conditions.append("level IN ('WARN', 'WARNING', 'ERROR', 'CRITICAL', 'ALERT', 'EMERGENCY')")

    if level and level.strip() and level.upper() != "ALL":
        # Supports filtering multiple levels or single
        lvl = level.strip().upper()
        if lvl == "ERROR_CRITICAL":
            conditions.append("level IN ('ERROR', 'CRITICAL', 'EMERGENCY', 'ALERT')")
        elif lvl == "CRITICAL_ALL":
            conditions.append("level IN ('CRITICAL', 'EMERGENCY', 'ALERT')")
        elif lvl in ("WARN", "WARNING"):
            conditions.append("level IN ('WARN', 'WARNING')")
        else:
            conditions.append("level = ?")
            params.append(lvl)

    if host and host.strip() and host.upper() != "ALL":
        conditions.append("host = ?")
        params.append(host.strip())

    excluded = list(dict.fromkeys(item.strip() for item in (exclude_hosts or []) if item.strip()))
    if excluded:
        conditions.append('host NOT IN (' + ','.join('?' for _ in excluded) + ')')
        params.extend(excluded)

    if app and app.strip() and app.upper() != "ALL":
        conditions.append("app = ?")
        params.append(app.strip())

    if search and search.strip():
        search_term = f"%{search.strip()}%"
        conditions.append("(message LIKE ? OR host LIKE ? OR app LIKE ? OR EXISTS (SELECT 1 FROM host_aliases WHERE host_aliases.host=logs.host AND name LIKE ?))")
        params.extend([search_term, search_term, search_term, search_term])

    if hours is not None:
        conditions.append("julianday(COALESCE(NULLIF(received_at,''),timestamp)) >= julianday('now', ?)")
        params.append(f'-{int(hours)} hours')

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    # 1. Total count query
    count_sql = f"SELECT COUNT(*) FROM logs {where_clause}"
    cursor = conn.cursor()
    cursor.execute(count_sql, params)
    total_count = cursor.fetchone()[0]

    sort_columns = {
        'received': 'id',  # Indexed insertion order, independent of device clocks.
        'timestamp': 'COALESCE(julianday(timestamp), julianday(received_at))',
        'level': "CASE level WHEN 'DEBUG' THEN 0 WHEN 'INFO' THEN 1 WHEN 'NOTICE' THEN 2 WHEN 'WARN' THEN 3 WHEN 'WARNING' THEN 3 WHEN 'ERROR' THEN 4 WHEN 'CRITICAL' THEN 5 WHEN 'ALERT' THEN 6 WHEN 'EMERGENCY' THEN 7 ELSE -1 END",
        'host': 'COALESCE((SELECT name FROM host_aliases WHERE host_aliases.host=logs.host),host) COLLATE NOCASE',
        'app': 'app COLLATE NOCASE',
        'message': 'message COLLATE NOCASE',
    }
    if sort_by not in sort_columns or sort_order not in ('asc', 'desc'):
        raise ValueError('Unsupported log sorting')
    order_expression = sort_columns[sort_by]
    direction = sort_order.upper()

    # 2. Paginated data query
    offset = max(0, (page - 1) * per_page)
    data_sql = f"""
        SELECT id, timestamp, host, app, level, facility, message, raw, metadata
        FROM logs
        {where_clause}
        ORDER BY {order_expression} {direction}, id {direction}
        LIMIT ? OFFSET ?
    """
    cursor.execute(data_sql, params + [per_page, offset])
    rows = cursor.fetchall()

    logs = [dict(row) for row in rows]
    aliases = get_host_aliases()
    for log in logs:
        log["display_host"] = aliases.get(log["host"], log["host"])
        try:
            log['metadata'] = json.loads(log['metadata'])
        except (ValueError, TypeError):
            log['metadata'] = {}
    return logs, total_count


def get_stats() -> Dict[str, Any]:
    """Computes real-time statistics for the dashboard cards."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Total count
    cursor.execute("SELECT COUNT(*) FROM logs")
    total = cursor.fetchone()[0]

    # Counts by level
    cursor.execute(
        """
        SELECT 
            SUM(CASE WHEN level IN ('CRITICAL', 'EMERGENCY', 'ALERT') THEN 1 ELSE 0 END) as critical_count,
            SUM(CASE WHEN level = 'ERROR' THEN 1 ELSE 0 END) as error_count,
            SUM(CASE WHEN level IN ('WARN', 'WARNING') THEN 1 ELSE 0 END) as warn_count,
            SUM(CASE WHEN level = 'INFO' THEN 1 ELSE 0 END) as info_count
        FROM logs
        """
    )
    row = cursor.fetchone()

    return {
        "total": total or 0,
        "critical": row["critical_count"] or 0,
        "error": row["error_count"] or 0,
        "warn": row["warn_count"] or 0,
        "info": row["info_count"] or 0,
        "active_hosts": sum(is_source_ip(item["host"]) for item in conn.execute("SELECT DISTINCT host FROM logs")),
    }


def is_source_ip(host):
    from ipaddress import ip_address
    try:
        ip_address(host)
        return True
    except (ValueError, TypeError):
        return False


def is_unassigned_syslog_host(host):
    """Old parser stored process TAGs as hosts; never guess their source device."""
    return bool(re.fullmatch(r'[A-Za-z_][\w.-]*(?:\[\d+\])?:', host))


def get_filter_options() -> Dict[str, Any]:
    """Returns distinct hosts and applications for UI dropdown filters."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT DISTINCT host FROM logs ORDER BY host ASC")
    hosts = [r["host"] for r in cursor.fetchall() if r["host"]]

    cursor.execute("SELECT DISTINCT app FROM logs ORDER BY app ASC")
    apps = [r["app"] for r in cursor.fetchall() if r["app"]]

    labels = {host: f'Nicht zugeordnet · {host}' for host in hosts if is_unassigned_syslog_host(host)}
    labels.update(get_host_aliases())
    reported_names = get_reported_source_names()
    verified_names = {row['ip'] for row in conn.execute('SELECT ip FROM source_dns')}
    verified_names.update(row['host'] for row in conn.execute('SELECT host FROM host_aliases'))
    from host_resolution import source_names
    verified_names.update(source_names())
    from ipaddress import ip_address
    ip_hosts, legacy_hosts = [], []
    for host in hosts:
        try:
            address = ip_address(host)
        except ValueError:
            legacy_hosts.append({'value':host, 'label':f'{host} · Quell-IP nicht gespeichert'})
            continue
        name = labels.get(host)
        if host in reported_names and host not in verified_names:
            name = f'{name} · gemeldet'
        ip_hosts.append({'value':host, 'label':f'{host} ({name})' if name and name != host else f'{host} (DNS nicht aufgelöst)', 'sort_name':name if name and name != host else host})
    ip_hosts.sort(key=lambda option: (ip_address(option['value']).version, int(ip_address(option['value']))))
    return {
        "hosts": hosts,
        "host_labels": labels,
        "host_groups": [{"label":"Geräte nach Quell-IP", "kind":"source_ip", "options":ip_hosts},
                        {"label":"Altbestand / interne Meldungen ohne Quell-IP", "kind":"unassigned", "options":legacy_hosts}],
        "apps": apps,
    }


def clear_all_logs() -> int:
    """Deletes all log entries from the database and returns deleted count."""
    conn = get_db_connection()
    with conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM logs")
        count = cursor.fetchone()[0]
        cursor.execute("DELETE FROM logs")
        cursor.execute("DELETE FROM incidents")
        cursor.execute("DELETE FROM analysis_cache")
    return count


def touch_monitored_host(host):
    import time
    conn = get_db_connection()
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT offline FROM monitored_hosts WHERE host=?", (host,)).fetchone()
        conn.execute("INSERT INTO monitored_hosts(host,last_seen,offline) VALUES(?,?,0) ON CONFLICT(host) DO UPDATE SET last_seen=excluded.last_seen,offline=0", (host,time.time()))
    return bool(row and row['offline'])


def mark_offline_hosts(timeout=300):
    import time
    conn = get_db_connection()
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute("SELECT host FROM monitored_hosts WHERE offline=0 AND last_seen < ?", (time.time()-timeout,)).fetchall()
        rows = [row for row in rows if is_source_ip(row['host'])]
        conn.executemany('UPDATE monitored_hosts SET offline=1 WHERE host=?', [(row['host'],) for row in rows])
    return [row['host'] for row in rows]


def get_monitored_hosts(include_legacy=False):
    return [dict(row) for row in get_db_connection().execute("SELECT * FROM monitored_hosts ORDER BY host")
            if include_legacy or is_source_ip(row['host'])]


def get_dashboard_summary():
    """Activity is based on receipt time, not a device's possibly incorrect clock."""
    import time
    now = time.time()
    conn = get_db_connection()
    recent = f"{RECEIPT_SQL} >= julianday('now', '-1 day')"
    # Count the receipt range using its existing expression index; read full
    # records only for warning/error levels, rather than every INFO/DEBUG row.
    totals = dict(logs_24h=conn.execute(f'SELECT COUNT(*) FROM logs WHERE {recent}').fetchone()[0],
                  errors_24h=0, error_only_24h=0, critical_24h=0, warnings_24h=0)
    errors_by_host = {}
    for row in conn.execute(f"""SELECT host,level,COUNT(*) AS n,SUM(app <> 'host-monitor') AS device_n FROM logs INDEXED BY idx_logs_level
            WHERE level IN ('WARN','WARNING','ERROR','CRITICAL','EMERGENCY','ALERT') AND {recent}
            GROUP BY host,level"""):
        level, count = row['level'], row['n']
        if level in ('WARN','WARNING'):
            totals['warnings_24h'] += count
        else:
            totals['errors_24h'] += count
            totals['error_only_24h' if level == 'ERROR' else 'critical_24h'] += count
            errors_by_host[row['host']] = errors_by_host.get(row['host'], 0) + row['device_n']
    # GROUP BY can count directly in the small covering host index. Previously
    # app filtering and per-row date conversion forced a full history table read.
    monitor_counts = {row['host']: row['n'] for row in conn.execute(
        "SELECT host,COUNT(*) AS n FROM logs WHERE app='host-monitor' GROUP BY host")}
    hosts = {}
    for row in conn.execute('SELECT host,COUNT(*) AS n FROM logs GROUP BY host'):
        host = row['host']
        count = row['n'] - monitor_counts.get(host, 0)
        if count <= 0:
            continue
        # received_at is assigned at insertion. Use the latest stored entry,
        # independent of the sending device's clock, with the existing host index.
        latest = conn.execute(f"""SELECT COALESCE(NULLIF(received_at,''),timestamp) AS last_message,
            CAST(strftime('%s',COALESCE(NULLIF(received_at,''),timestamp)) AS REAL) AS last_activity,
            {RECEIPT_SQL} >= julianday('now','-15 minutes') AS recent
            FROM logs WHERE host=? AND app <> 'host-monitor' ORDER BY id DESC LIMIT 1""", (host,)).fetchone()
        hosts[host] = dict(host=host, log_count=count, errors_24h=errors_by_host.get(host,0), **dict(latest))
    from resource_metrics import latest as latest_resources
    resources = latest_resources(conn)
    for host, sample in resources.items():
        hosts.setdefault(host, dict(host=host, log_count=0, last_message=None, recent=0, errors_24h=0))
    for host in hosts.values():
        host['resources'] = resources.get(host['host'])
    for heartbeat in get_monitored_hosts(include_legacy=True):
        host = hosts.setdefault(heartbeat['host'], dict(host=heartbeat['host'], log_count=0, last_message=None, recent=0, errors_24h=0))
        host['heartbeat_last_seen'] = heartbeat['last_seen']
        host['status'] = 'offline' if now - heartbeat['last_seen'] > 300 else 'online'
    for host in hosts.values():
        host.setdefault('heartbeat_last_seen', None)
        host.setdefault('status', 'syslog')
    aliases = get_host_aliases()
    dns_names = {row['ip']: row['name'] for row in conn.execute('SELECT ip,name FROM source_dns')}
    reported_names = get_reported_source_names()
    explicit_names = {row['host'] for row in conn.execute('SELECT host FROM host_aliases')}
    from host_resolution import source_names
    explicit_names.update(source_names())
    from ipaddress import ip_address
    for host in hosts.values():
        try:
            host['source_ip'] = str(ip_address(host['host']))
        except ValueError:
            host['source_ip'] = None
        host['dns_name'] = dns_names.get(host['host'])
        host['name_source'] = 'manual' if host['host'] in explicit_names else 'dns' if host['host'] in dns_names else 'reported' if host['host'] in reported_names else 'ip'
        host["display_host"] = aliases.get(host["host"], host["host"])
    unassigned = [host for host in hosts.values() if host['source_ip'] is None]
    devices = sorted((host for host in hosts.values() if host['source_ip'] is not None), key=lambda h: (h['status'] != 'offline', -max(h['heartbeat_last_seen'] or 0, h.get('last_activity') or 0), h['host'].lower()))
    def entries(extra='', prioritize=False):
        query = f"""SELECT id,timestamp,host,app,level,facility,message,raw,metadata
            FROM logs WHERE NOT (app='resource-monitor' AND message='HEARTBEAT') {extra}"""
        if prioritize:
            # Equality on level uses its existing index (including rowid order).
            # Limit each level before ranking, instead of sorting a day's logs.
            rows = []
            ranks = {'EMERGENCY': 0, 'ALERT': 1, 'CRITICAL': 2, 'ERROR': 3, 'WARN': 4, 'WARNING': 4}
            for level in ranks:
                rows.extend(conn.execute(query + ' AND level=? ORDER BY id DESC LIMIT 6', (level,)).fetchall())
            rows.sort(key=lambda row: (ranks[row['level']], -row['id']))
            rows = rows[:6]
        else:
            rows = conn.execute(query + ' ORDER BY id DESC LIMIT 6').fetchall()
        result = [dict(row) for row in rows]
        for entry in result:
            entry['display_host'] = aliases.get(entry['host'], entry['host'])
            entry['metadata'] = json.loads(entry['metadata'] or '{}')
        return result
    from incidents import open_count
    return dict(**totals, open_incidents=open_count(), hosts=devices, unassigned_hosts=unassigned, known_hosts=len(devices),
        recent_hosts=sum(bool(host['recent']) for host in devices),
        heartbeat_hosts=sum(host['heartbeat_last_seen'] is not None for host in devices),
        offline_hosts=sum(host['status']=='offline' for host in devices),
        latest=entries(), attention=entries(f"AND {recent} AND level IN ('WARN','WARNING','ERROR','CRITICAL','EMERGENCY','ALERT')", prioritize=True))


def get_ingestion_metrics():
    """Rolling receipt-time counts; five-minute rate is an average, not a counter."""
    conn = get_db_connection()
    receipt = RECEIPT_SQL
    counts = dict(conn.execute(f"""SELECT
        COALESCE(SUM({receipt} >= julianday('now','-1 minute')),0) AS logs_last_minute,
        COALESCE(SUM({receipt} >= julianday('now','-5 minutes')),0) AS logs_last_5_minutes,
        COALESCE(SUM({receipt} >= julianday('now','-1 minute') AND app='resource-monitor' AND message='HEARTBEAT'),0) AS heartbeats_last_minute
        FROM logs WHERE {receipt} >= julianday('now','-5 minutes')""").fetchone())
    counts['total_logs'] = conn.execute('SELECT COUNT(*) FROM logs').fetchone()[0]
    counts['logs_per_minute_5m'] = round(counts.pop('logs_last_5_minutes') / 5, 1)
    counts['events_last_minute'] = counts['logs_last_minute'] - counts['heartbeats_last_minute']
    return counts


def get_reported_source_names():
    from metrics_cache import snapshot
    def collect():
        from host_resolution import reported_hostname
        from ipaddress import ip_address
        conn = get_db_connection()
        result = {}
        for row in conn.execute('SELECT DISTINCT host FROM logs'):
            host = row['host']
            if not is_source_ip(host):
                continue
            names = {}
            for log in conn.execute('SELECT metadata FROM logs WHERE host=? ORDER BY id DESC LIMIT 30', (host,)):
                try:
                    metadata = json.loads(log['metadata'] or '{}')
                    if not isinstance(metadata,dict) or str(ip_address(metadata.get('source_ip'))) != host:
                        continue
                    name = reported_hostname(metadata)
                    if name:
                        names.setdefault(name.casefold(),name)
                except (ValueError,TypeError):
                    continue
            if len(names) == 1:
                result[host] = next(iter(names.values()))
        return result
    return snapshot('reported_source_names',15,collect)


def get_host_aliases():
    conn = get_db_connection()
    names = get_reported_source_names()
    names.update({row['ip']: row['name'] for row in conn.execute('SELECT ip,name FROM source_dns')})
    try:
        configured = json.loads(get_setting('syslog_source_names', '{}'))
        if isinstance(configured, dict):
            names.update({ip:name for ip,name in configured.items() if isinstance(name,str) and name.strip()})
    except (ValueError, TypeError):
        pass
    names.update({row['host']: row['name'] for row in conn.execute('SELECT host,name FROM host_aliases')})
    return names


def set_host_alias(host, name):
    host, name = host.strip(), name.strip()
    conn = get_db_connection()
    with conn:
        if name:
            conn.execute('INSERT INTO host_aliases(host,name) VALUES(?,?) ON CONFLICT(host) DO UPDATE SET name=excluded.name', (host,name))
        else:
            conn.execute('DELETE FROM host_aliases WHERE host=?', (host,))
    return {'host':host, 'display_host':name or host}


def get_host_display_name(host):
    row = get_db_connection().execute('SELECT name FROM host_aliases WHERE host=?', (host,)).fetchone()
    return row['name'] if row else host


def claim_rule_notification(key, cooldown, now):
    conn = get_db_connection()
    with conn:
        cursor = conn.execute("""INSERT INTO rule_notifications(rule_id,host,app,channel,last_sent) VALUES(?,?,?,?,?)
            ON CONFLICT(rule_id,host,app,channel) DO UPDATE SET last_sent=excluded.last_sent
            WHERE rule_notifications.last_sent <= ?""", (*key, now, now-cooldown))
    return cursor.rowcount == 1


def release_rule_notification(key, now):
    conn = get_db_connection()
    with conn:
        conn.execute('DELETE FROM rule_notifications WHERE rule_id=? AND host=? AND app=? AND channel=? AND last_sent=?', (*key,now))
