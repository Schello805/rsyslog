"""Recognize explicit application log prefixes without keyword guessing."""
import re

_PREFIX = re.compile(r'^(?:\d{4}[/-]\d{2}[/-]\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?\s+)?(?:\[(ERR|ERROR|WRN|WARN|WARNING|FTL|FATAL|CRITICAL)\]|(ERR|ERROR|WRN|WARN|WARNING|FTL|FATAL|CRITICAL)(?=\s|:|$))')
_SCORES = {'DEBUG': 0, 'INFO': 1, 'NOTICE': 2, 'WARN': 3, 'WARNING': 3, 'ERROR': 4, 'CRITICAL': 5, 'ALERT': 6, 'EMERGENCY': 7}


def effective_level(level, message, metadata=None):
    # Structured CEF severity remains authoritative.
    if (metadata or {}).get('event_id') is not None:
        return level
    match = _PREFIX.match(str(message or '').strip())
    if not match:
        return level
    marker = match.group(1) or match.group(2)
    candidate = 'ERROR' if marker in ('ERR', 'ERROR') else 'CRITICAL' if marker in ('FTL','FATAL','CRITICAL') else 'WARN'
    return candidate if _SCORES.get(candidate, 0) > _SCORES.get(level, 1) else level
