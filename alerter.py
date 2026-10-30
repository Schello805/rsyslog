"""
Alerting system for RSyslog Web.
Dispatches critical notifications to Telegram and/or Ntfy with threshold-based
triggers (e.g., immediate, after 2+ occurrences), host-filtering, and spam cooldowns.
"""

import html
import logging
import time
from typing import Any, Dict, List, Optional
import httpx
from config import config
import database

logger = logging.getLogger("rsyslog.alerter")

# In-memory history of recent incident timestamps for thresholds: fingerprint -> [ts1, ts2, ...]
_incident_timestamps: Dict[str, List[float]] = {}

# In-memory history of last sent alerts for throttling cooldown: fingerprint -> last_sent_ts
_last_sent_timestamps: Dict[str, float] = {}


def get_severity_score(level: str) -> int:
    """Numerical hierarchy for log severities."""
    hierarchy = {
        "DEBUG": 10,
        "INFO": 20,
        "NOTICE": 25,
        "WARN": 30,
        "WARNING": 30,
        "ERROR": 40,
        "CRITICAL": 50,
        "EMERGENCY": 60,
        "ALERT": 60,
    }
    return hierarchy.get(level.upper(), 20)


def should_evaluate_level(log_level: str, min_level: str) -> bool:
    """Checks if the log level meets or exceeds the configured minimum severity."""
    return get_severity_score(log_level) >= get_severity_score(min_level)


def is_host_ignored(host: str, ignore_list_str: str) -> bool:
    """Checks if host is in the comma-separated ignore list."""
    if not ignore_list_str or not ignore_list_str.strip():
        return False
    ignored = [h.strip().lower() for h in ignore_list_str.split(",") if h.strip()]
    return host.lower() in ignored


def record_incident_and_check_threshold(fingerprint: str, threshold_count: int, window_minutes: int) -> int:
    """
    Records an incident timestamp and returns the count of occurrences within window.
    Cleans up old timestamps.
    """
    now = time.time()
    cutoff = now - (window_minutes * 60)

    if fingerprint not in _incident_timestamps:
        _incident_timestamps[fingerprint] = []

    # Filter out entries older than the window
    _incident_timestamps[fingerprint] = [t for t in _incident_timestamps[fingerprint] if t >= cutoff]
    _incident_timestamps[fingerprint].append(now)

    return len(_incident_timestamps[fingerprint])


def is_in_cooldown(fingerprint: str, cooldown_seconds: int) -> bool:
    """Checks if alert is still in the cooldown period."""
    now = time.time()
    last_sent = _last_sent_timestamps.get(fingerprint, 0)
    if now - last_sent < cooldown_seconds:
        return True
    return False


def mark_alert_sent(fingerprint: str) -> None:
    """Updates the last sent timestamp for a given alert fingerprint."""
    _last_sent_timestamps[fingerprint] = time.time()


async def send_telegram_alert(
    log: Dict[str, Any],
    token: str,
    chat_id: str,
    occurrence_count: int = 1,
    window_minutes: int = 5,
) -> bool:
    """Sends a formatted alert message via Telegram Bot API."""
    if not token or not chat_id:
        return False

    level_emoji = "🔥" if log.get("level") in ("CRITICAL", "EMERGENCY", "ALERT") else "⚠️"
    msg_preview = html.escape(str(log.get("message", ""))[:1200])
    host_clean = html.escape(str(log.get("host", "unknown")))
    app_clean = html.escape(str(log.get("app", "syslog")))
    lvl_clean = html.escape(str(log.get("level", "ERROR")))
    ts_clean = html.escape(str(log.get("timestamp", "")))

    threshold_notice = ""
    if occurrence_count > 1:
        threshold_notice = f"\n📊 <b>Schwelle erreicht:</b> {occurrence_count}. Auftreten innerhalb von {window_minutes} Min!\n"

    text = (
        f"{level_emoji} <b>Syslog Alarm: {lvl_clean}</b>\n"
        f"{threshold_notice}\n"
        f"🖥 <b>Host:</b> <code>{host_clean}</code>\n"
        f"📦 <b>Dienst:</b> <code>{app_clean}</code>\n"
        f"⏰ <b>Zeit:</b> {ts_clean}\n\n"
        f"📝 <b>Nachricht:</b>\n<pre>{msg_preview}</pre>"
    )

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                logger.info(f"Telegram alert sent successfully for {host_clean}/{app_clean}")
                return True
            else:
                logger.warning(f"Telegram alert failed ({resp.status_code}): {resp.text}")
    except Exception as e:
        logger.error(f"Error sending Telegram alert: {e}")

    return False


async def send_ntfy_alert(
    log: Dict[str, Any],
    topic: str,
    server: str = "https://ntfy.sh",
    occurrence_count: int = 1,
    window_minutes: int = 5,
) -> bool:
    """Sends a push notification via Ntfy (https://ntfy.sh or self-hosted)."""
    if not topic:
        return False

    srv = (server or "https://ntfy.sh").rstrip("/")
    url = f"{srv}/{topic}"

    is_crit = log.get("level") in ("CRITICAL", "EMERGENCY", "ALERT")
    priority = "5" if is_crit else "4"
    tags = "rotating_light,fire" if is_crit else "warning,bell"

    title = f"Syslog {log.get('level')}: {log.get('host')} - {log.get('app')}"
    if occurrence_count > 1:
        title += f" ({occurrence_count}x in {window_minutes}m)"

    body = f"[{log.get('timestamp')}]\n{log.get('message')}"

    headers = {
        "Title": title,
        "Priority": priority,
        "Tags": tags,
    }

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(url, content=body.encode("utf-8"), headers=headers)
            if resp.status_code == 200:
                logger.info("Ntfy alert sent successfully.")
                return True
            else:
                logger.warning(f"Ntfy alert failed ({resp.status_code}): {resp.text}")
    except Exception as e:
        logger.error(f"Error sending Ntfy alert: {e}")

    return False


async def process_alert(log: Dict[str, Any]) -> None:
    """
    Main alerting pipeline: loads settings, checks level threshold,
    evaluates frequency trigger, applies cooldown, and sends notifications.
    """
    settings = database.get_all_settings()

    min_level = settings.get("alert_min_level", "ERROR")
    log_level = log.get("level", "INFO")
    if not should_evaluate_level(log_level, min_level):
        return

    host = log.get("host", "unknown")
    if is_host_ignored(host, settings.get("alert_ignore_hosts", "")):
        logger.debug(f"Alert ignored for host: {host}")
        return

    # Evaluate occurrences within the time window
    app = log.get("app", "syslog")
    fingerprint = f"{host}:{app}:{log_level.upper()}"
    threshold_count = max(1, int(settings.get("alert_threshold_count", 1)))
    window_minutes = max(1, int(settings.get("alert_threshold_window_minutes", 5)))
    cooldown_seconds = max(5, int(settings.get("alert_throttle_seconds", 30)))

    current_occurrences = record_incident_and_check_threshold(fingerprint, threshold_count, window_minutes)

    # Check if threshold is reached (e.g., at least 2 errors)
    if current_occurrences < threshold_count:
        logger.debug(
            f"Incident recorded for {fingerprint}: {current_occurrences}/{threshold_count} "
            f"(waiting for threshold before alerting)"
        )
        return

    # Check cooldown / rate limiting
    if is_in_cooldown(fingerprint, cooldown_seconds):
        logger.debug(f"Alert for {fingerprint} in cooldown (occurrences: {current_occurrences})")
        return

    # Mark as sent
    mark_alert_sent(fingerprint)

    # 1. Telegram
    if settings.get("telegram_enabled") and settings.get("telegram_bot_token") and settings.get("telegram_chat_id"):
        await send_telegram_alert(
            log=log,
            token=settings["telegram_bot_token"],
            chat_id=settings["telegram_chat_id"],
            occurrence_count=current_occurrences,
            window_minutes=window_minutes,
        )

    # 2. Ntfy
    if settings.get("ntfy_enabled") and settings.get("ntfy_topic"):
        await send_ntfy_alert(
            log=log,
            topic=settings["ntfy_topic"],
            server=settings.get("ntfy_server", "https://ntfy.sh"),
            occurrence_count=current_occurrences,
            window_minutes=window_minutes,
        )


async def test_telegram_config(token: str, chat_id: str) -> Dict[str, Any]:
    """Tests specified Telegram credentials."""
    if not token or not chat_id:
        return {"success": False, "error": "Bot-Token oder Chat-ID fehlt."}

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": "✅ <b>RSyslog Web Testnachricht</b>\nDie Verbindung zu deinem Telegram-Bot ist erfolgreich eingerichtet!",
        "parse_mode": "HTML",
    }
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                return {"success": True, "message": "Testnachricht erfolgreich an Telegram gesendet!"}
            else:
                return {"success": False, "error": f"Telegram API Fehler ({resp.status_code}): {resp.text}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def test_ntfy_config(topic: str, server: str = "https://ntfy.sh") -> Dict[str, Any]:
    """Tests specified Ntfy configuration."""
    if not topic:
        return {"success": False, "error": "Ntfy-Thema (Topic) fehlt."}

    srv = (server or "https://ntfy.sh").rstrip("/")
    url = f"{srv}/{topic}"

    headers = {
        "Title": "RSyslog Web Testnachricht",
        "Priority": "4",
        "Tags": "white_check_mark,bell",
    }
    body = "Die Verbindung zu Ntfy ist erfolgreich eingerichtet! Du empfängst ab sofort Syslog-Benachrichtigungen."

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(url, content=body.encode("utf-8"), headers=headers)
            if resp.status_code == 200:
                return {"success": True, "message": "Testnachricht erfolgreich an Ntfy gesendet!"}
            else:
                return {"success": False, "error": f"Ntfy Fehler ({resp.status_code}): {resp.text}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def send_test_alert() -> Dict[str, Any]:
    """Test function using current saved settings."""
    settings = database.get_all_settings()
    results = {}

    if settings.get("telegram_enabled") and settings.get("telegram_bot_token") and settings.get("telegram_chat_id"):
        res = await test_telegram_config(settings["telegram_bot_token"], settings["telegram_chat_id"])
        results["telegram"] = True if res["success"] else res["error"]
    else:
        results["telegram"] = "Telegram ist deaktiviert oder unvollständig."

    if settings.get("ntfy_enabled") and settings.get("ntfy_topic"):
        res = await test_ntfy_config(settings["ntfy_topic"], settings.get("ntfy_server", "https://ntfy.sh"))
        results["ntfy"] = True if res["success"] else res["error"]
    else:
        results["ntfy"] = "Ntfy ist deaktiviert oder Thema ist leer."

    return results
