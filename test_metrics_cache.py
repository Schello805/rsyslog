"""Snapshots deduplicate concurrent readers without sharing mutable results."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch
import metrics_cache as cache


class MetricsCacheTests(unittest.TestCase):
    def setUp(self):
        cache.invalidate()

    def tearDown(self):
        cache.invalidate()

    def test_concurrent_readers_share_one_collection_and_get_copies(self):
        collect = Mock(return_value={'hosts': []})
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda _: cache.snapshot('dashboard', 10, collect), range(16)))
        collect.assert_called_once()
        results[0]['hosts'].append('changed')
        self.assertEqual(cache.snapshot('dashboard', 10, collect), {'hosts': []})

    def test_expiry_invalidation_and_failure_retry(self):
        collect = Mock(side_effect=[1, 2, ValueError('failed'), 3])
        with patch.object(cache, 'monotonic', return_value=100):
            self.assertEqual(cache.snapshot('dashboard', 10, collect), 1)
        with patch.object(cache, 'monotonic', return_value=110):
            self.assertEqual(cache.snapshot('dashboard', 10, collect), 2)
            cache.invalidate()
            with self.assertRaises(ValueError):
                cache.snapshot('dashboard', 10, collect)
            self.assertEqual(cache.snapshot('dashboard', 10, collect), 3)


if __name__ == '__main__':
    unittest.main()
