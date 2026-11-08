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

    def test_retention_uses_receipt_time_batches_and_invalidates_metrics(self):
        from metrics_cache import snapshot
        conn = database.get_db_connection()
        # A wrong device clock must not delete a newly received record.
        database.insert_log('192.0.2.1','app','INFO','recent',timestamp='2000-01-01T00:00:00Z')
        with conn:
            conn.executemany("INSERT INTO logs(timestamp,received_at,host,app,level,message) VALUES('2099-01-01','2000-01-01','192.0.2.2','app','INFO','old')", [()] * 2005)
        self.assertEqual(snapshot('cleanup-test',60,lambda:database.get_logs()[1]),2006)
        self.assertEqual(database.delete_logs_older_than(14),2005)
        self.assertEqual(snapshot('cleanup-test',60,lambda:database.get_logs()[1]),1)
        self.assertEqual(database.get_logs()[0][0]['message'],'recent')
        self.assertEqual(database.delete_logs_older_than(14),0)

    def test_manual_cleanup_api_validates_and_reports_real_results(self):
        import asyncio
        import httpx
        import main
        async def check():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://test') as client:
                result = await client.post('/api/database/cleanup',json={'days':14})
                self.assertEqual(result.status_code,200)
                self.assertEqual(result.json()['deleted'],0)
                self.assertIn('reusable_bytes',result.json()['storage'])
                result = await client.post('/api/database/cleanup',json={'mode':'size'})
                self.assertEqual(result.json()['storage']['limit_bytes'],0)
                self.assertEqual((await client.post('/api/database/cleanup',json={'days':-1})).status_code,422)
                self.assertEqual((await client.post('/api/database/cleanup',json={'days':14},headers={'Origin':'http://other'})).status_code,403)

                # Vacuum API
                vac_res = await client.post('/api/database/vacuum')
                self.assertEqual(vac_res.status_code, 200)
                self.assertTrue(vac_res.json()['success'])
                self.assertEqual((await client.post('/api/database/vacuum', headers={'Origin': 'http://evil.com'})).status_code, 403)
        asyncio.run(check())

    def test_save_settings_triggers_immediate_retention_and_cleans_legacy_timestamps(self):
        conn = database.get_db_connection()
        with conn:
            # Insert a legacy unparseable BSD timestamp with empty received_at from long ago
            conn.execute("INSERT INTO logs(timestamp,received_at,host,app,level,message) VALUES('Jan  1 00:00:00','','host','app','INFO','legacy_old')")
        # And a recent log
        database.insert_log('host', 'app', 'INFO', 'current_log')
        self.assertEqual(database.count_logs_older_than(7), 1)

        import asyncio
        import httpx
        import main
        async def check():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
                res = await client.post('/api/settings', json={'retention_days': 7})
                self.assertEqual(res.status_code, 200)
                # Verify old log was immediately deleted on save!
                self.assertEqual(database.count_logs_older_than(7), 0)
                logs, total = database.get_logs()
                self.assertEqual(total, 1)
                self.assertEqual(logs[0]['message'], 'current_log')
        asyncio.run(check())

    def test_automatic_cleanup_survives_status_failure(self):
        import asyncio
        from unittest.mock import AsyncMock
        import monitoring
        async def stop_after_one(_):
            stop_after_one.calls += 1
            if stop_after_one.calls > 1:
                raise asyncio.CancelledError
        stop_after_one.calls = 0
        async def run_in_place(fn,*args):
            return fn(*args)
        with patch.object(monitoring.asyncio,'sleep',side_effect=stop_after_one), patch.object(monitoring.asyncio,'to_thread',side_effect=run_in_place), patch.object(database,'mark_offline_hosts',side_effect=RuntimeError('status locked')), patch.object(database,'delete_logs_older_than',return_value=0) as retention, patch('storage_policy.trim_database',return_value=0) as size, patch('host_resolution.refresh_known_sources'), patch.object(monitoring.logger,'exception'):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(monitoring.maintenance())
            retention.assert_called_once()
            size.assert_called_once()

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
        trace = []
        database.get_db_connection().set_trace_callback(trace.append)
        self.assertGreater(storage_policy.trim_database(),0)
        database.get_db_connection().set_trace_callback(None)
        self.assertFalse(any('SELECT COUNT(*) FROM logs' in sql for sql in trace))
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


    def test_sqlite_full_rolls_back_incoming_log_and_counts_discard(self):
        import ingestion_counters
        conn=database.get_db_connection()
        pages=conn.execute('PRAGMA page_count').fetchone()[0]
        conn.execute(f'PRAGMA max_page_count={pages}')
        before=ingestion_counters.snapshot()['storage']
        self.assertIsNone(database.insert_log('host','app','WARN','x'*1000000,apply_ingestion_rules=True))
        self.assertEqual(ingestion_counters.snapshot()['storage'],before+1)
        self.assertEqual(database.get_stats()['total'],0)
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM incidents').fetchone()[0],0)


if __name__ == '__main__':
    unittest.main()
