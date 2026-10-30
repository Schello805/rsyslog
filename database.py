"""
SQLite Database Layer for RSyslog Web.
Provides thread-safe connections, WAL mode for high performance,
indices for filtering/search, and log query aggregation.
"""

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
        # Performance indexes for dashboard filtering and sorting
        conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON logs(timestamp DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_level ON logs(level)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_host ON logs(host)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_app ON logs(app)")


def insert_log(
    host: str,
    app: str,
    level: str,
    message: str,
    facility: Optional[str] = "user",
    raw: Optional[str] = None,
    timestamp: Optional[str] = None,
) -> Dict[str, Any]:
    """Inserts a new log entry and returns the inserted record as a dict."""
    if not timestamp:
        timestamp = datetime.now(timezone.utc).isoformat()

    normalized_level = level.upper().strip()
    clean_host = (host or "unknown").strip()
    clean_app = (app or "syslog").strip()
    clean_msg = (message or "").strip()

    conn = get_db_connection()
    with conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO logs (timestamp, host, app, level, facility, message, raw)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp,
                clean_host,
                clean_app,
                normalized_level,
                facility or "user",
                clean_msg,
                raw or clean_msg,
            ),
        )
        log_id = cursor.lastrowid

    return {
        "id": log_id,
        "timestamp": timestamp,
        "host": clean_host,
        "app": clean_app,
        "level": normalized_level,
        "facility": facility or "user",
        "message": clean_msg,
        "raw": raw or clean_msg,
    }


def get_logs(
    page: int = 1,
    per_page: int = 50,
    level: Optional[str] = None,
    host: Optional[str] = None,
    app: Optional[str] = None,
    search: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Retrieves filtered logs with pagination.
    Returns (records, total_matching_records).
    """
    conn = get_db_connection()
    conditions = []
    params: List[Any] = []

    if level and level.strip() and level.upper() != "ALL":
        # Supports filtering multiple levels or single
        lvl = level.strip().upper()
        if lvl == "ERROR_CRITICAL":
            conditions.append("level IN ('ERROR', 'CRITICAL', 'EMERGENCY', 'ALERT')")
        else:
            conditions.append("level = ?")
            params.append(lvl)

    if host and host.strip() and host.upper() != "ALL":
        conditions.append("host = ?")
        params.append(host.strip())

    if app and app.strip() and app.upper() != "ALL":
        conditions.append("app = ?")
        params.append(app.strip())

    if search and search.strip():
        search_term = f"%{search.strip()}%"
        conditions.append("(message LIKE ? OR host LIKE ? OR app LIKE ?)")
        params.extend([search_term, search_term, search_term])

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    # 1. Total count query
    count_sql = f"SELECT COUNT(*) FROM logs {where_clause}"
    cursor = conn.cursor()
    cursor.execute(count_sql, params)
    total_count = cursor.fetchone()[0]

    # 2. Paginated data query
    offset = max(0, (page - 1) * per_page)
    data_sql = f"""
        SELECT id, timestamp, host, app, level, facility, message, raw
        FROM logs
        {where_clause}
        ORDER BY id DESC
        LIMIT ? OFFSET ?
    """
    cursor.execute(data_sql, params + [per_page, offset])
    rows = cursor.fetchall()

    logs = [dict(row) for row in rows]
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


def get_filter_options() -> Dict[str, List[str]]:
    """Returns distinct hosts and applications for UI dropdown filters."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT DISTINCT host FROM logs ORDER BY host ASC")
    hosts = [r["host"] for r in cursor.fetchall() if r["host"]]

    cursor.execute("SELECT DISTINCT app FROM logs ORDER BY app ASC")
    apps = [r["app"] for r in cursor.fetchall() if r["app"]]

    return {
        "hosts": hosts,
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
        cursor.execute("VACUUM")
    return count
