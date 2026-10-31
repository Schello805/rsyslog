"""
SQLite Database Layer for RSyslog Web.
Provides thread-safe connections, WAL mode for high performance,
indices for filtering/search, and log query aggregation.
"""

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from config import config

_local = threading.local()


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
        # Performance indexes for dashboard filtering and sorting
        conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON logs(timestamp DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_level ON logs(level)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_host ON logs(host)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_app ON logs(app)")

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

    repair_legacy_syslog_hosts()
    repair_configured_source_hosts()


def repair_legacy_syslog_hosts():
    """One-time repair of IP fallbacks with an unambiguous BSD hostname in raw data.

    Do not merge by source IP or display alias: relays can send for multiple hosts.
    Raw messages, receipt times and user-assigned names remain intact.
    """
    from ipaddress import ip_address
    from syslog_parser import RFC3164_HEADER_REGEX, parse_syslog_message
    conn = get_db_connection()
    marker = 'migration_syslog_hostname_v1'
    if conn.execute('SELECT value FROM settings WHERE key=?', (marker,)).fetchone():
        return 0
    last_id, repaired = 0, 0
    while True:
        rows = conn.execute("SELECT id,host,raw FROM logs WHERE id>? AND app='syslog' AND raw LIKE '<%' ORDER BY id LIMIT 500", (last_id,)).fetchall()
        if not rows:
            break
        with conn:
            for row in rows:
                last_id = row['id']
                try:
                    ip_address(row['host'])
                except ValueError:
                    continue
                if not RFC3164_HEADER_REGEX.match(row['raw'].strip()):
                    continue
                parsed = parse_syslog_message(row['raw'], row['host'])
                hostname = parsed['host']
                if hostname in (row['host'], '-', 'unknown') or parsed.get('metadata'):
                    continue
                try:
                    ip_address(hostname)
                    continue
                except ValueError:
                    pass
                conn.execute('UPDATE logs SET host=?,app=?,message=? WHERE id=?',
                    (hostname, parsed['app'], parsed['message'], row['id']))
                repaired += 1
    with conn:
        conn.execute('INSERT INTO settings(key,value) VALUES(?,?)', (marker, str(repaired)))
    return repaired


def repair_configured_source_hosts():
    """Repair hostname-less historic records only with an explicit operator mapping."""
    import hashlib
    from syslog_parser import parse_syslog_message
    try:
        mapping = json.loads(get_setting('syslog_legacy_host_names', '{}'))
        sources = json.loads(get_setting('syslog_source_names', '{}'))
    except (ValueError, TypeError):
        return 0
    if not isinstance(mapping, dict) or not mapping:
        return 0
    marker = 'migration_source_names_' + hashlib.sha256(json.dumps(mapping, sort_keys=True).encode()).hexdigest()[:16]
    if get_setting(marker):
        return 0
    conn = get_db_connection()
    repaired = 0
    for old, name in mapping.items():
        if not isinstance(name, str) or not name.strip():
            continue
        last_id = 0
        while True:
            rows = conn.execute('SELECT id,raw,metadata FROM logs WHERE host=? AND id>? ORDER BY id LIMIT 500', (old,last_id)).fetchall()
            if not rows:
                break
            with conn:
                for row in rows:
                    last_id = row['id']
                    parsed = parse_syslog_message(row['raw'], name)
                    if parsed['host'] != name:
                        continue
                    try:
                        metadata = json.loads(row['metadata'] or '{}')
                    except (ValueError, TypeError):
                        metadata = {}
                    if not isinstance(metadata, dict):
                        metadata = {}
                    ips = [ip for ip, host in sources.items() if host == name] if isinstance(sources, dict) else []
                    if len(ips) == 1:
                        metadata['source_ip'] = ips[0]
                    conn.execute('UPDATE logs SET host=?,app=?,message=?,metadata=? WHERE id=?',
                                 (name, parsed['app'], parsed['message'], json.dumps(metadata), row['id']))
                    repaired += 1
    set_setting(marker, repaired or 'done')
    return repaired


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
        # Retention
        "retention_days": as_int(db_settings.get("retention_days"), 0),
        "discard_debug": as_bool(db_settings.get("discard_debug"), True),
    }


def save_all_settings(settings: Dict[str, Any]) -> None:
    """Persists a dictionary of settings into the SQLite database."""
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


def delete_logs_older_than(days: int) -> int:
    """Deletes logs older than a given number of days. If days <= 0, does nothing."""
    if days <= 0:
        return 0
    from datetime import datetime, timezone, timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    conn = get_db_connection()
    with conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM logs WHERE julianday(timestamp) < julianday(?)", (cutoff,))
        count = cursor.fetchone()[0]
        cursor.execute("DELETE FROM logs WHERE julianday(timestamp) < julianday(?)", (cutoff,))
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
) -> Optional[Dict[str, Any]]:
    """Inserts a new log entry and returns the inserted record as a dict."""
    if not timestamp:
        timestamp = datetime.now(timezone.utc).isoformat()

    # Structured HTTP clients may place a complete CEF packet in message as well.
    if isinstance(message, str) and message.lstrip('\ufeff').startswith(('CEF:0|', 'CEF:1|')) and not metadata:
        from syslog_parser import parse_syslog_message
        parsed = parse_syslog_message(message, client_ip=host)
        if parsed.get('metadata'):
            raw = raw or parsed['raw']
            host, app, level, message = parsed['host'], parsed['app'], parsed['level'], parsed['message']
            metadata = parsed['metadata']
    normalized_level = level.upper().strip()
    if (apply_ingestion_rules and normalized_level == 'DEBUG'
            and str(get_setting('discard_debug', 'true')).lower() in ('true', '1', 'yes', 'on')):
        return None
    clean_host = (host or "unknown").strip()
    clean_app = (app or "syslog").strip()
    clean_msg = (message or "").strip()

    conn = get_db_connection()
    with conn:
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

    return {
        "id": log_id,
        "timestamp": timestamp,
        "host": clean_host,
        "display_host": get_host_display_name(clean_host),
        "app": clean_app,
        "level": normalized_level,
        "facility": facility or "user",
        "message": clean_msg,
        "raw": raw or clean_msg,
        "metadata": metadata or {},
    }


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
            SUM(CASE WHEN level = 'INFO' THEN 1 ELSE 0 END) as info_count,
            COUNT(DISTINCT host) as active_hosts
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
        "active_hosts": row["active_hosts"] or 0,
    }


def get_filter_options() -> Dict[str, Any]:
    """Returns distinct hosts and applications for UI dropdown filters."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT DISTINCT host FROM logs ORDER BY host ASC")
    hosts = [r["host"] for r in cursor.fetchall() if r["host"]]

    cursor.execute("SELECT DISTINCT app FROM logs ORDER BY app ASC")
    apps = [r["app"] for r in cursor.fetchall() if r["app"]]

    return {
        "hosts": hosts,
        "host_labels": get_host_aliases(),
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
    return count


def touch_monitored_host(host):
    import time
    conn = get_db_connection()
    with conn:
        row = conn.execute("SELECT offline FROM monitored_hosts WHERE host=?", (host,)).fetchone()
        conn.execute("INSERT INTO monitored_hosts(host,last_seen,offline) VALUES(?,?,0) ON CONFLICT(host) DO UPDATE SET last_seen=excluded.last_seen,offline=0", (host,time.time()))
    return bool(row and row['offline'])


def mark_offline_hosts(timeout=300):
    import time
    conn = get_db_connection()
    with conn:
        rows = conn.execute("SELECT host FROM monitored_hosts WHERE offline=0 AND last_seen < ?", (time.time()-timeout,)).fetchall()
        conn.execute("UPDATE monitored_hosts SET offline=1 WHERE offline=0 AND last_seen < ?", (time.time()-timeout,))
    return [row['host'] for row in rows]


def get_monitored_hosts():
    return [dict(row) for row in get_db_connection().execute("SELECT * FROM monitored_hosts ORDER BY host")]


def get_dashboard_summary():
    """Activity is based on receipt time, not a device's possibly incorrect clock."""
    import time
    now = time.time()
    conn = get_db_connection()
    recent = "julianday(COALESCE(NULLIF(received_at, ''), timestamp)) >= julianday('now', '-1 day')"
    totals = dict(conn.execute(f"""SELECT COUNT(*) AS logs_24h,
        COALESCE(SUM(level IN ('ERROR','CRITICAL','EMERGENCY','ALERT')),0) AS errors_24h,
        COALESCE(SUM(level = 'ERROR'),0) AS error_only_24h,
        COALESCE(SUM(level IN ('CRITICAL','EMERGENCY','ALERT')),0) AS critical_24h,
        COALESCE(SUM(level IN ('WARN','WARNING')),0) AS warnings_24h
        FROM logs WHERE {recent}""").fetchone())
    rows = conn.execute(f"""SELECT host, COUNT(*) AS log_count,
        MAX(COALESCE(NULLIF(received_at,''),timestamp)) AS last_message,
        MAX(CAST(strftime('%s',COALESCE(NULLIF(received_at,''),timestamp)) AS REAL)) AS last_activity,
        MAX(julianday(COALESCE(NULLIF(received_at,''),timestamp))) >= julianday('now','-15 minutes') AS recent,
        SUM(CASE WHEN {recent} AND level IN ('ERROR','CRITICAL','EMERGENCY','ALERT') THEN 1 ELSE 0 END) AS errors_24h
        FROM logs WHERE app <> 'host-monitor' GROUP BY host""").fetchall()
    hosts = {row['host']: dict(row) for row in rows}
    for heartbeat in get_monitored_hosts():
        host = hosts.setdefault(heartbeat['host'], dict(host=heartbeat['host'], log_count=0, last_message=None, recent=0, errors_24h=0))
        host['heartbeat_last_seen'] = heartbeat['last_seen']
        host['status'] = 'offline' if now - heartbeat['last_seen'] > 300 else 'online'
    for host in hosts.values():
        host.setdefault('heartbeat_last_seen', None)
        host.setdefault('status', 'syslog')
    aliases = get_host_aliases()
    for host in hosts.values():
        host["display_host"] = aliases.get(host["host"], host["host"])
    devices = sorted(hosts.values(), key=lambda h: (h['status'] != 'offline', -max(h['heartbeat_last_seen'] or 0, h.get('last_activity') or 0), h['host'].lower()))
    def entries(extra=''):
        rows = conn.execute(f"""SELECT id,timestamp,host,app,level,facility,message,raw,metadata
            FROM logs WHERE NOT (app='resource-monitor' AND message='HEARTBEAT') {extra}
            ORDER BY id DESC LIMIT 6""").fetchall()
        result = [dict(row) for row in rows]
        for entry in result:
            entry['display_host'] = aliases.get(entry['host'], entry['host'])
            entry['metadata'] = json.loads(entry['metadata'] or '{}')
        return result
    return dict(**totals, hosts=devices, known_hosts=len(devices),
        recent_hosts=sum(bool(host['recent']) for host in devices),
        heartbeat_hosts=sum(host['heartbeat_last_seen'] is not None for host in devices),
        offline_hosts=sum(host['status']=='offline' for host in devices),
        latest=entries(), attention=entries(f"AND {recent} AND level IN ('WARN','WARNING','ERROR','CRITICAL','EMERGENCY','ALERT')"))


def get_ingestion_metrics():
    """Rolling receipt-time counts; five-minute rate is an average, not a counter."""
    conn = get_db_connection()
    receipt = "julianday(COALESCE(NULLIF(received_at,''),timestamp))"
    counts = dict(conn.execute(f"""SELECT COUNT(*) AS total_logs,
        COALESCE(SUM({receipt} >= julianday('now','-1 minute')),0) AS logs_last_minute,
        COALESCE(SUM({receipt} >= julianday('now','-5 minutes')),0) AS logs_last_5_minutes,
        COALESCE(SUM({receipt} >= julianday('now','-1 minute') AND app='resource-monitor' AND message='HEARTBEAT'),0) AS heartbeats_last_minute
        FROM logs""").fetchone())
    counts['logs_per_minute_5m'] = round(counts.pop('logs_last_5_minutes') / 5, 1)
    counts['events_last_minute'] = counts['logs_last_minute'] - counts['heartbeats_last_minute']
    return counts


def get_host_aliases():
    return {row['host']: row['name'] for row in get_db_connection().execute('SELECT host,name FROM host_aliases')}


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
