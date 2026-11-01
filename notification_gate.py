"""Bounded per-device/channel notification budget; higher severity bypasses it."""
from collections import OrderedDict, deque
import hashlib
import re
import time

_hosts = OrderedDict()
MAX_HOST_CHANNELS = 4096
MAX_GROUPS = 128


def reset():
    _hosts.clear()


def group_key(log):
    return hashlib.sha256((log.get('app','') + '\0' + re.sub(r'\s+', ' ', log.get('message','')).strip()).encode()).hexdigest()


def is_escalation(log, cooldown=30):
    from alerter import get_severity_score
    score = get_severity_score(log.get('level', 'INFO'))
    now = time.monotonic()
    group = group_key(log)
    for channel in ('telegram', 'ntfy'):
        state = _hosts.get((log.get('host','unknown'), channel))
        if state is None:
            continue
        old = state['groups'].get(group)
        if old and now - old['at'] < cooldown and score > old['score']:
            return True
        recent = [item[1] for item in state['sent'] if item[0] > now - 60]
        if recent and score > max(recent):
            return True
    return False


def reserve(log, channel, cooldown, limit=5):
    from alerter import get_severity_score
    now = time.monotonic()
    key = (log.get('host', 'unknown'), channel)
    state = _hosts.setdefault(key, {'sent': deque(), 'groups': OrderedDict()})
    _hosts.move_to_end(key)
    while len(_hosts) > MAX_HOST_CHANNELS:
        _hosts.popitem(last=False)
    while state['sent'] and state['sent'][0][0] <= now - 60:
        state['sent'].popleft()
    score = get_severity_score(log.get('level', 'INFO'))
    group = group_key(log)
    old = state['groups'].get(group)
    escalation = ((old is not None and now - old['at'] < cooldown and score > old['score'])
                  or (bool(state['sent']) and score > max(item[1] for item in state['sent'])))
    if old and now - old['at'] < cooldown and score <= old['score']:
        old['suppressed'] += 1
        return None
    if len(state['sent']) >= limit and not escalation:
        if old:
            old['suppressed'] += 1
        return None
    ticket = (now, score, object())
    previous = old
    state['groups'][group] = {'at': now, 'score': score, 'suppressed': 0, 'token': ticket[2]}
    state['groups'].move_to_end(group)
    while len(state['groups']) > MAX_GROUPS:
        state['groups'].popitem(last=False)
    state['sent'].append(ticket)  # Reserve before awaiting a slow network channel.
    return (state, group, ticket, previous)


def finish(reservation, success):
    if success:
        return
    state, group, ticket, previous = reservation
    try:
        state['sent'].remove(ticket)
    except ValueError:
        pass
    if state['groups'].get(group, {}).get('token') is ticket[2]:
        if previous is None:
            state['groups'].pop(group, None)
        else:
            state['groups'][group] = previous


async def send(log, channel, callback, cooldown):
    reservation = reserve(log, channel, cooldown)
    if reservation is None:
        return False
    try:
        success = await callback()
    except BaseException:
        finish(reservation, False)
        raise
    finish(reservation, success)
    return success


def forget_host(host):
    for key in list(_hosts):
        if key[0] == host:
            _hosts.pop(key, None)
