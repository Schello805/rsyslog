"""Operational controls use isolated SQLite databases, never a running installation."""
import os
import tempfile
import unittest
from unittest.mock import patch
import database
from config import config


class OperationalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = getattr(database._local, 'connection', None)
        database._local.connection = None
        self.override = patch.object(config, 'DATABASE_PATH', os.path.join(self.temp.name, 'db.sqlite'))
        self.override.start(); database.init_db()

    def tearDown(self):
        database._local.connection.close(); database._local.connection = self.previous
        self.override.stop(); self.temp.cleanup()

    def test_debug_counter_counts_only_discarded_intake(self):
        import ingestion_counters
        before = ingestion_counters.snapshot()['debug']
        self.assertIsNone(database.insert_log('host','app','DEBUG','noise',apply_ingestion_rules=True))
        self.assertEqual(ingestion_counters.snapshot()['debug'], before+1)
        database.save_all_settings({'discard_debug':False})
        self.assertIsNotNone(database.insert_log('host','app','DEBUG','needed',apply_ingestion_rules=True))
        self.assertEqual(ingestion_counters.snapshot()['debug'], before+1)

    def test_size_limit_warns_reclaims_oldest_and_resumes_intake(self):
        import ingestion_counters
        import storage_policy
        self.assertEqual(database.get_all_settings()['max_database_mb'],0)
        database.save_all_settings({'max_database_mb':16})
        message = 'x' * (2*1024*1024)
        for n in range(5): database.insert_log('host','app','INFO',message+str(n))
        self.assertTrue(storage_policy.status()['warning'])
        before = ingestion_counters.snapshot()['storage']
        self.assertIsNone(database.insert_log('host','app','INFO','new',apply_ingestion_rules=True))
        self.assertEqual(ingestion_counters.snapshot()['storage'],before+1)
        self.assertGreater(storage_policy.trim_database(),0)
        self.assertIsNotNone(database.insert_log('host','app','INFO','new',apply_ingestion_rules=True))
        rows,total=database.get_logs(sort_by='timestamp',sort_order='asc')
        self.assertFalse(any(row['message'].endswith('0') for row in rows))
        self.assertEqual(rows[-1]['message'],'new')
        self.assertGreater(storage_policy.status()['reusable_bytes'],0)
        for bad in (-1,1,True,'bad'):
            with self.assertRaises(ValueError): database.save_all_settings({'max_database_mb':bad})
