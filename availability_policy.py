"""Suppress device availability notices, without hiding service/network failures."""
import re

_DEVICE_STATE = re.compile(
    r'^(?:(?:device|client|host|gerät)\s+(?:.+?\s+)?)'
    r'(?:is\s+|ist\s+)?(?:offline|unreachable|not reachable|nicht erreichbar)'
    r'(?:[.!]|\s+since\b.*|\s+seit\b.*)?$', re.IGNORECASE)


def is_availability_notice(app, message, metadata=None):
    text = str(message or '').strip()
    if app == 'host-monitor' and text in (
        'ALARM: Seit 5 Minuten kein Heartbeat',
        'ENTWARNUNG: Heartbeat wieder empfangen',
    ):
        return True
    event = str((metadata or {}).get('event_name', '')).strip()
    return bool(_DEVICE_STATE.fullmatch(event) or _DEVICE_STATE.fullmatch(text))
