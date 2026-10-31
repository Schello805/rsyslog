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


    def test_incidents_group_resolve_reopen_and_backfill_without_duplicates(self):
        import asyncio, httpx, main
        for _ in range(3): database.insert_log('nas','backup','ERROR','Connection failed')
        database.insert_log('other','backup','ERROR','Connection failed')
        database.insert_log('nas','backup','INFO','fine')
        async def check():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://test') as client:
                result=(await client.get('/api/incidents')).json()
                self.assertEqual(result['total'],2)
                incident=next(item for item in result['incidents'] if item['host']=='nas')
                self.assertEqual(incident['occurrences'],3)
                response=await client.post('/api/incidents/'+incident['fingerprint'],json={'status':'resolved'})
                self.assertEqual(response.status_code,200)
                self.assertEqual((await client.get('/api/incidents?state=resolved')).json()['total'],1)
                database.insert_log('nas','backup','ERROR','Connection failed')
                result=(await client.get('/api/incidents?search=nas')).json()
                self.assertEqual(result['incidents'][0]['status'],'open')
                self.assertEqual(result['incidents'][0]['occurrences'],4)
                database.init_db()
                self.assertEqual((await client.get('/api/incidents?search=nas')).json()['incidents'][0]['occurrences'],4)
                database.clear_all_logs()
                self.assertEqual((await client.get('/api/incidents')).json()['total'],0)
        asyncio.run(check())

    def test_incident_migration_backfills_existing_records_once(self):
        import incidents
        conn=database.get_db_connection()
        with conn:
            conn.execute("DELETE FROM settings WHERE key='migration_incidents_v1'")
            for _ in range(4):
                conn.execute("INSERT INTO logs(timestamp,received_at,host,app,level,message) VALUES('2026-01-01','2026-01-01','old','app','WARN','same')")
        incidents.initialize(conn); incidents.initialize(conn)
        row=conn.execute('SELECT occurrences FROM incidents').fetchone()
        self.assertEqual(row[0],4)
