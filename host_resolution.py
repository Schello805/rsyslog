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
    """Transport peer is the identity; packet hostname is untrusted context only."""
    from database import remember_source_dns
    try:
        address = str(ip_address(address))
    except ValueError:
        return entry
    metadata = dict(entry.get('metadata') or {})
    if entry.get('host') and entry['host'] != address:
        metadata['reported_host'] = entry['host']
    metadata['source_ip'] = address
    entry['metadata'] = metadata
    entry['host'] = address
    name = resolve_ip(address)
    if name != address:
        remember_source_dns(address, name)
    return entry
