"""Literal message rules, independent of the global severity threshold."""
import time
from typing import List
from pydantic import BaseModel, Field, field_validator
import database


class MessageRule(BaseModel):
    id: str = Field(..., min_length=1, max_length=64, pattern=r'^[a-zA-Z0-9_-]+$')
    name: str = Field(..., min_length=1, max_length=80)
    enabled: bool = True
    host: str = Field('', max_length=255)
    app: str = Field('', max_length=128)
    contains: List[str] = Field(..., min_length=1, max_length=20)
    excludes: List[str] = Field(default_factory=list, max_length=20)
    cooldown_seconds: int = Field(3600, ge=60, le=604800)

    @field_validator('contains', 'excludes')
    @classmethod
    def clean_terms(cls, values):
        terms = list(dict.fromkeys(value.strip() for value in values))
        if any(not term or len(term) > 200 for term in terms):
            raise ValueError('Suchtexte müssen zwischen 1 und 200 Zeichen lang sein')
        return terms

    @field_validator('name', 'host', 'app')
    @classmethod
    def clean_text(cls, value):
        return value.strip()


def validate_rules(value):
    if not isinstance(value, list) or len(value) > 50:
        raise ValueError('Maximal 50 Zusatzregeln sind möglich')
    rules = [MessageRule.model_validate(rule).model_dump() for rule in value]
    if len({rule['id'] for rule in rules}) != len(rules) or any(not rule['name'] for rule in rules):
        raise ValueError('Regeln benötigen eindeutige IDs und einen Namen')
    return rules


def matches(rule, log):
    message = str(log.get('message', '')).casefold()
    return (rule.get('enabled', True)
            and (not rule.get('host') or rule['host'] == log.get('host'))
            and (not rule.get('app') or rule['app'] == log.get('app'))
            and any(term.casefold() in message for term in rule.get('contains', []))
            and not any(term.casefold() in message for term in rule.get('excludes', [])))


async def process_message_rules(log, settings):
    """First matching rule owns this notification; successful channels have persistent cooldowns."""
    from alerter import send_ntfy_alert, send_telegram_alert
    for rule in settings.get('alert_rules', []):
        if not matches(rule, log):
            continue
        payload = dict(log, message=f"[{rule['name']}] {log.get('message', '')}")
        channels = []
        if settings.get('telegram_enabled') and settings.get('telegram_bot_token') and settings.get('telegram_chat_id'):
            channels.append(('telegram', lambda: send_telegram_alert(payload, settings['telegram_bot_token'], settings['telegram_chat_id'])))
        if settings.get('ntfy_enabled') and settings.get('ntfy_topic'):
            channels.append(('ntfy', lambda: send_ntfy_alert(payload, settings['ntfy_topic'], settings.get('ntfy_server', 'https://ntfy.sh'))))
        for channel, send in channels:
            now = time.time()
            from alerter import get_severity_score
            key = (rule['id'] + ':' + str(get_severity_score(log.get('level','INFO'))), log.get('host','unknown'), log.get('app','syslog'), channel)
            if not database.claim_rule_notification(key, rule['cooldown_seconds'], now):
                continue
            try:
                from notification_gate import send as send_grouped
                success = await send_grouped(log, channel, send, rule['cooldown_seconds'])
            except Exception:
                success = False
            if not success:
                database.release_rule_notification(key, now)
        return True
    return False
