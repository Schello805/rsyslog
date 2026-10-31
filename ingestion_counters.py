"""Thread-safe counters, scoped to this application process."""
from collections import Counter
import threading

_counts = Counter()
_lock = threading.Lock()


def discarded(reason):
    with _lock:
        _counts[reason] += 1


def snapshot():
    with _lock:
        return {'debug': _counts['debug'], 'storage': _counts['storage']}
