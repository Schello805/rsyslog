"""Resolve hostname-less syslog without unbounded DNS work in receiver threads."""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from ipaddress import ip_address
import json
import re
import socket
import threading
import time

_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='syslog-dns')
_cache = OrderedDict()
_pending = {}
_lock = threading.Lock()


def source_names():
    from database import get_setting
    try:
        names = json.loads(get_setting('syslog_source_names', '{}'))
        return names if isinstance(names, dict) else {}
    except (ValueError, TypeError):
        return {}


def _dns_name(future, address):
    try:
        name = future.result()[0].rstrip('.')
        return name if re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,252}', name) else address
    except (OSError, ValueError, IndexError):
        return address


def _complete(address, future):
    name = _dns_name(future, address)
    with _lock:
        _pending.pop(address, None)
        _cache[address] = (time.monotonic() + (600 if name != address else 60), name)
        _cache.move_to_end(address)
        while len(_cache) > 512:
            _cache.popitem(last=False)


def resolve_ip(address):
    created = False
    with _lock:
        cached = _cache.get(address)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        future = _pending.get(address)
        if future is None:
            if len(_pending) >= 2:
                return address
            future = _pool.submit(socket.gethostbyaddr, address)
            _pending[address] = future
            created = True
    if created:
        future.add_done_callback(lambda task: _complete(address, task))
    try:
        future.result(timeout=0.2)
        return _dns_name(future, address)
    except TimeoutError:
        return address
    except OSError:
        return address


def identify_source(entry, address):
    from database import get_db_connection, get_host_display_name
    try:
        ip_address(address)
    except ValueError:
        return entry
    entry['metadata'] = dict(entry.get('metadata') or {}, source_ip=address)
    target = entry['host']
    try:
        ip_address(target)
    except ValueError:
        return entry  # Keep a hostname carried by the sender/CEF event.
    configured = source_names().get(target)
    if isinstance(configured, str) and configured.strip():
        entry['host'] = configured.strip()
        return entry
    if get_host_display_name(target) != target:
        return entry  # Preserve explicitly assigned names on an IP host.
    name = resolve_ip(target)
    if name != target:
        # Use an already known spelling/short name to avoid DNS case/FQDN duplicates.
        row = get_db_connection().execute('SELECT host FROM logs WHERE host=? COLLATE NOCASE OR host=? COLLATE NOCASE ORDER BY host LIMIT 1', (name, name.split('.')[0])).fetchone()
        entry['host'] = row['host'] if row else name
    return entry
