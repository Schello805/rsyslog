"""Small shared snapshots; concurrent browsers perform one refresh per interval."""
from copy import deepcopy
from threading import RLock
from time import monotonic
from config import config

_lock = RLock()
_entries = {}


def snapshot(name, seconds, collect):
    key = (str(config.DATABASE_PATH), name)
    with _lock:
        now = monotonic()
        cached = _entries.get(key)
        if cached is None or now - cached[0] >= seconds:
            value = collect()  # Failures are retried, never cached.
            _entries[key] = (monotonic(), value)
        return deepcopy(_entries[key][1])


def invalidate():
    with _lock:
        _entries.clear()
