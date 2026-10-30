"""
Alerting system for RSyslog Web.
Dispatches critical notifications to Telegram and/or Ntfy with throttling and deduplication.
"""

import html
import logging
import time
from typing import Any, Dict, Optional
import httpx
from config import config

logger = logging.getLogger("rsyslog.alerter")

# In-memory alert history for throttling: (host, app, level) -> last_sent_timestamp
_last_alerts: Dict[str, float] = {}


def should_alert(level: str) -> bool:
    """Checks whether the log level meets or exceeds the alert threshold."""
    alert_hierarchy = {
        "DEBUG": 10,
        "INFO": 20,
        "NOTICE": 25,
        "WARN": 30,
        "ERROR": 40,
        "CRITICAL": 50,
        "EMERGENCY": 60,
        "ALERT": 60,
    }
    min_threshold = alert_hierarchy.get(config.ALERT_MIN_LEVEL, 40)
    current_val = alert_hierarchy.get(level.upper(), 20)
    return current_val >= min_threshold


def _is_throttled(fingerprint: str) -> bool:
    """Checks if an alert for this fingerprint was sent too recently."""
    now = time.time()
    last_time = _last_alerts.get(fingerprint, 0)
    if now - last_time < config.ALERT_THROTTLE_SECONDS:
        return True
    _last_alerts[fingerprint] = now
    return False


async def send_telegram_alert(log: Dict[str, Any]) -> bool:
    """Sends a formatted alert message via Telegram Bot API."""
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        return False

    level_emoji = "🔥" if log.get("level") in ("CRITICAL", "EMERGENCY", "ALERT") else "⚠️"
    msg_preview = html.escape(str(log.get("message", ""))[:1200])
    host_clean = html.escape(str(log.get("host", "unknown")))
    app_clean = html.escape(str(log.get("app", "syslog")))
    lvl_clean = html.escape(str(log.get("level", "ERROR")))
    ts_clean = html.escape(str(log.get("timestamp", "")))

    text = (
        f"{level_emoji} <b>Syslog Alarm: {lvl_clean}</b>\n\n"
        f"🖥 <b>Host:</b> <code>{host_clean}</code>\n"
        f"📦 <b>Dienst:</b> <code>{app_clean}</code>\n"
        f"⏰ <b>Zeit:</b> {ts_clean}\n\n"
        f"📝 <b>Nachricht:</b>\n<pre>{msg_preview}</pre>"
    )

    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": config.TELEGRAM_CHAT_ID,
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


async def send_ntfy_alert(log: Dict[str, Any]) -> bool:
    """Sends a push notification via Ntfy (https://ntfy.sh)."""
    if not config.NTFY_TOPIC:
        return False

    server = config.NTFY_SERVER.rstrip("/")
    url = f"{server}/{config.NTFY_TOPIC}"
    
    is_crit = log.get("level") in ("CRITICAL", "EMERGENCY", "ALERT")
    priority = "5" if is_crit else "4"
    tags = "warning,rotating_light" if is_crit else "warning"

    headers = {
        "Title": f"Syslog {log.get('level')}: {log.get('host')} - {log.get('app')}",
        "Priority": priority,
        "Tags": tags,
    }

    body = f"[{log.get('timestamp')}]\n{log.get('message')}"

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
    """Evaluates and dispatches alerts across configured channels."""
    level = log.get("level", "INFO")
    if not should_alert(level):
        return

    # Fingerprint to throttle duplicates
    fingerprint = f"{log.get('host')}:{log.get('app')}:{level}"
    if _is_throttled(fingerprint):
        logger.debug(f"Alert throttled for {fingerprint}")
        return

    # Dispatch to Telegram and/or Ntfy concurrently
    if config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID:
        await send_telegram_alert(log)

    if config.NTFY_TOPIC:
        await send_ntfy_alert(log)


async def send_test_alert() -> Dict[str, Any]:
    """Test function to verify notification setup."""
    sample_log = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "host": "test-host.local",
        "app": "rsyslog-monitor-test",
        "level": "CRITICAL",
        "message": "Dies ist ein Testalarm von RSyslog Web zur Überprüfung der Benachrichtigungskanäle.",
    }

    results = {}
    if config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID:
        results["telegram"] = await send_telegram_alert(sample_log)
    else:
        results["telegram"] = "Nicht konfiguriert (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID leer)"

    if config.NTFY_TOPIC:
        results["ntfy"] = await send_ntfy_alert(sample_log)
    else:
        results["ntfy"] = "Nicht konfiguriert (NTFY_TOPIC leer)"

    return results
