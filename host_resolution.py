"""Resolve hostname-less syslog without unbounded DNS work in receiver threads."""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from ipaddress import ip_address
import json
import re
import socket
import threading
import time
import dns.resolver
from dns.exception import DNSException

_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='syslog-dns')
_cache = OrderedDict()
_pending = {}
_lock = threading.Lock()
_generation = 0
_hints = OrderedDict()


def source_names():
    from database import get_setting
    try:
        names = json.loads(get_setting('syslog_source_names', '{}'))
        return names if isinstance(names, dict) else {}
    except (ValueError, TypeError):
        return {}


def clean_hostname(value):
    if not isinstance(value, str):
        return None
    value = value.strip().rstrip('.')
    if len(value) > 253 or not value or not all(re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_-]{0,62}', part) for part in value.split('.')):
        return None
    try:
        ip_address(value)
        return None
    except ValueError:
        return value


def reported_hostname(metadata):
    return clean_hostname(metadata.get('reporting_host') if metadata.get('format') == 'CEF' else metadata.get('reported_host'))


def reset_dns_cache():
    global _generation
    with _lock:
        _generation += 1
        _cache.clear()  # Running jobs remain bounded; old results are ignored.


def _lookup_name(address, hints):
    from database import get_setting
    servers = [server.strip() for server in get_setting('dns_servers','').split(',') if server.strip()]
    domain = clean_hostname(get_setting('dns_search_domain',''))
    resolver = None
    if servers:
        resolver = dns.resolver.Resolver(configure=False)
        resolver.nameservers = servers
        resolver.timeout = 0.7
        resolver.lifetime = 1.5
    try:
        if resolver:
            answer = resolver.resolve_address(address)
            name = clean_hostname(str(answer[0].target))
            if name:
                return (name, [], [address])
        else:
            return socket.gethostbyaddr(address)
    except (OSError, DNSException):
        pass
    # Candidate names are never identities. Only a matching A/AAAA record is accepted.
    for hint in hints[:2]:
        candidates = [hint + '.' + domain, hint] if domain and '.' not in hint else [hint]
        for candidate in candidates:
            try:
                if resolver:
                    answer = resolver.resolve(candidate, 'A' if ip_address(address).version == 4 else 'AAAA')
                    addresses = [str(record.address) for record in answer]
                    name = clean_hostname(str(answer.canonical_name)) or candidate
                else:
                    records = socket.getaddrinfo(candidate, None, socket.AF_INET if ip_address(address).version == 4 else socket.AF_INET6)
                    addresses = [record[4][0] for record in records]
                    name = candidate
                if any(ip_address(result) == ip_address(address) for result in addresses):
                    return (name, [], [address])
            except (OSError, DNSException, ValueError):
                continue
    return (address, [], [address])


def _dns_name(future, address):
    try:
        name = future.result()[0].rstrip('.')
        return name if re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,252}', name) else address
    except Exception:
        return address  # A failed DNS worker must never break log intake.


def _complete(address, future, generation):
    name = _dns_name(future, address)
    with _lock:
        if _pending.get(address, (None,))[0] is future:
            _pending.pop(address, None)
        if generation != _generation:
            return
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
        item = _pending.get(address)
        if item is None:
            if len(_pending) >= 2:
                return address
            generation = _generation
            future = _pool.submit(_lookup_name, address, _hints.get(address, ()))
            _pending[address] = (future, generation)
            created = True
        else:
            future, generation = item
            if generation != _generation:
                return address
    if created:
        future.add_done_callback(lambda task: _complete(address, task, generation))
    try:
        future.result(timeout=0.2)
        with _lock:
            if generation != _generation:
                return address
        return _dns_name(future, address)
    except TimeoutError:
        return address
    except Exception:
        return address


def remember_hint(address, name):
    name = clean_hostname(name)
    if not name:
        return
    with _lock:
        values = _hints.get(address, ())
        _hints[address] = tuple(dict.fromkeys((name, *values)))[:2]
        _hints.move_to_end(address)
        while len(_hints) > 512:
            _hints.popitem(last=False)


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
    hint = reported_hostname(metadata)
    remember_hint(address, hint)
    metadata['source_ip'] = address
    entry['metadata'] = metadata
    entry['host'] = address
    name = resolve_ip(address)
    if name != address:
        remember_source_dns(address, name)
    return entry


_refresh_cursor = 0


def refresh_known_sources(batch_size=8):
    """Refresh a small rotating DNS batch, including senders with no new packets.

    Late DNS answers used to stay only in RAM until another packet arrived.
    This maintenance path persists them and retries sources skipped by a busy pool.
    DNS changes only labels; no host identity is changed or combined.
    """
    global _refresh_cursor
    from database import get_db_connection, remember_source_dns, get_reported_source_names
    reported_names = get_reported_source_names()
    addresses = []
    for row in get_db_connection().execute('SELECT DISTINCT host FROM logs ORDER BY host'):
        try:
            ip_address(row['host'])
        except ValueError:
            continue
        addresses.append(row['host'])
    if not addresses:
        return 0
    changed = 0
    count = min(batch_size, len(addresses))
    for index in range(count):
        address = addresses[(_refresh_cursor + index) % len(addresses)]
        remember_hint(address, reported_names.get(address))
        name = resolve_ip(address)
        if name != address:
            changed += bool(remember_source_dns(address, name))
    _refresh_cursor = (_refresh_cursor + count) % len(addresses)
    if changed:
        from metrics_cache import invalidate
        invalidate()
    return changed
