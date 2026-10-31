"""Dashboard activity must include syslog senders without inventing reachability."""
import os
import tempfile
import time
import unittest
from unittest.mock import patch
_suite_database = tempfile.TemporaryDirectory()
os.environ['DATABASE_PATH'] = os.path.join(_suite_database.name, 'suite.db')
import database
from config import config
import version


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.previous = getattr(database._local, 'connection', None)
        database._local.connection = None
        self.override = patch.object(config, 'DATABASE_PATH', os.path.join(self.directory.name, 'dashboard.db'))
        self.override.start()
        database.init_db()

    def tearDown(self):
        if database._local.connection:
            database._local.connection.close()
        database._local.connection = self.previous
        self.override.stop()
        self.directory.cleanup()

    def test_critical_card_counts_and_filter_match_without_double_counting_errors(self):
        for level in ['ERROR', 'CRITICAL', 'ALERT', 'EMERGENCY', 'WARN']:
            database.insert_log('host', 'app', level, level)
        summary = database.get_dashboard_summary()
        self.assertEqual(summary['error_only_24h'], 1)
        self.assertEqual(summary['critical_24h'], 3)
        self.assertEqual(summary['warnings_24h'], 1)
        self.assertEqual(database.get_logs(level='CRITICAL_ALL', hours=24)[1], 3)
        self.assertEqual(database.get_logs(level='ERROR', hours=24)[1], 1)
        with database.get_db_connection() as connection:
            connection.execute("UPDATE logs SET received_at='2000-01-01' WHERE level='ALERT'")
        self.assertEqual(database.get_dashboard_summary()['critical_24h'], 2)
        self.assertEqual(database.get_logs(level='CRITICAL_ALL', hours=24)[1], 2)

    def test_syslog_senders_are_visible_without_heartbeat(self):
        database.insert_log('qnap', 'storage', 'WARN', 'low space')
        result = database.get_dashboard_summary()
        self.assertEqual(result['known_hosts'], 1)
        self.assertEqual(result['heartbeat_hosts'], 0)
        self.assertEqual(result['hosts'][0]['status'], 'syslog')
        self.assertEqual(result['recent_hosts'], 1)
        self.assertEqual(result['warnings_24h'], 1)

    def test_receipt_time_controls_windows_and_heartbeat_is_not_an_event(self):
        database.insert_log('linux', 'service', 'ERROR', 'clock is wrong', timestamp='2000-01-01T00:00:00Z')
        database.insert_log('linux', 'resource-monitor', 'INFO', 'HEARTBEAT')
        database.touch_monitored_host('linux')
        database.touch_monitored_host('offline')
        with database.get_db_connection() as connection:
            connection.execute('UPDATE monitored_hosts SET last_seen=? WHERE host=?', (time.time()-600, 'offline'))
        result = database.get_dashboard_summary()
        self.assertEqual(result['errors_24h'], 1)
        self.assertEqual(result['heartbeat_hosts'], 2)
        self.assertEqual(result['offline_hosts'], 1)
        self.assertEqual(len(result['latest']), 1)
        self.assertEqual(result['hosts'][0]['host'], 'offline')

    def test_old_events_do_not_count_as_current_attention(self):
        database.insert_log('old', 'app', 'ERROR', 'old')
        with database.get_db_connection() as connection:
            connection.execute("UPDATE logs SET received_at='2000-01-01T00:00:00+00:00'")
        result = database.get_dashboard_summary()
        self.assertEqual(result['errors_24h'], 0)
        self.assertEqual(result['recent_hosts'], 0)
        self.assertEqual(result['attention'], [])
        self.assertEqual(result['known_hosts'], 1)
        self.assertEqual(database.get_logs(level='ERROR_CRITICAL', hours=24)[1], 0)
        self.assertEqual(database.get_logs(level='ERROR_CRITICAL')[1], 1)

    def test_dashboard_api_and_time_filtered_logs(self):
        import asyncio
        import httpx
        import main
        database.insert_log('switch', 'network', 'WARNING', 'port issue')
        async def check():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
                summary = (await client.get('/api/dashboard')).json()
                self.assertEqual(summary['known_hosts'], 1)
                self.assertEqual(summary['warnings_24h'], 1)
                logs = (await client.get('/api/logs?level=WARN&hours=24')).json()
                self.assertEqual(logs['total'], 1)
                exported = (await client.get('/api/logs/export?format=json&level=WARN&hours=24')).json()
                self.assertEqual(len(exported), 1)
                invalid = await client.get('/api/logs?sort_by=invalid')
                self.assertEqual(invalid.status_code, 422)
                sorted_export = await client.get('/api/logs/export?format=json&sort_by=host&sort_order=asc')
                self.assertEqual(sorted_export.status_code, 200)
            def close():
                database._local.connection.close()
                database._local.connection = None
            await asyncio.to_thread(close)
        asyncio.run(check())

    def test_sorting_is_global_and_uses_severity_and_real_time(self):
        database.insert_log('Zulu', 'z-service', 'INFO', 'z-message', timestamp='2020-01-01T00:00:00+02:00')
        database.insert_log('alpha', 'a-service', 'CRITICAL', 'a-message', timestamp='2019-12-31T23:00:00Z')
        database.insert_log('Bravo', 'b-service', 'WARN', 'b-message', timestamp='2020-01-01T00:00:00Z')
        first, total = database.get_logs(sort_by='host', sort_order='asc', per_page=1)
        second, _ = database.get_logs(sort_by='host', sort_order='asc', per_page=1, page=2)
        self.assertEqual(total, 3)
        self.assertEqual([first[0]['host'], second[0]['host']], ['alpha', 'Bravo'])
        rows, _ = database.get_logs(sort_by='level', sort_order='desc')
        self.assertEqual([row['level'] for row in rows], ['CRITICAL', 'WARN', 'INFO'])
        rows, _ = database.get_logs(sort_by='timestamp', sort_order='asc')
        self.assertEqual([row['host'] for row in rows], ['Zulu', 'alpha', 'Bravo'])
        for column in ('app', 'message'):
            rows, _ = database.get_logs(sort_by=column, sort_order='asc')
            self.assertEqual(rows[0]['host'], 'alpha')
        with self.assertRaises(ValueError):
            database.get_logs(sort_by='id; DROP TABLE logs')

    def test_ingestion_metrics_use_receipt_time_and_separate_heartbeats(self):
        database.insert_log('new', 'service', 'INFO', 'new', timestamp='2000-01-01T00:00:00Z')
        database.insert_log('new', 'resource-monitor', 'INFO', 'HEARTBEAT')
        old = database.insert_log('old', 'service', 'INFO', 'old')
        with database.get_db_connection() as connection:
            connection.execute("UPDATE logs SET received_at=datetime('now','-3 minutes') WHERE id=?", (old['id'],))
        metrics = database.get_ingestion_metrics()
        self.assertEqual(metrics['total_logs'], 3)
        self.assertEqual(metrics['logs_last_minute'], 2)
        self.assertEqual(metrics['events_last_minute'], 1)
        self.assertEqual(metrics['heartbeats_last_minute'], 1)
        self.assertEqual(metrics['logs_per_minute_5m'], 0.6)

    def test_storage_and_system_api(self):
        import asyncio
        import httpx
        import main
        import system_metrics
        from collections import namedtuple
        database.insert_log('qnap', 'storage', 'INFO', 'test')
        disk = namedtuple('Disk', 'total used free')(1000, 600, 400)
        with patch.object(system_metrics.shutil, 'disk_usage', return_value=disk):
            metrics = system_metrics.collect_system_metrics()
            self.assertEqual(metrics['storage']['used_percent'], 60)
            self.assertEqual(metrics['storage']['free_bytes'], 400)
            self.assertGreater(metrics['database']['database_bytes'], 0)
            self.assertEqual(metrics['database']['total_bytes'], sum(metrics['database'][key] for key in ('database_bytes','wal_bytes','shared_memory_bytes')))
            self.assertEqual(metrics['known_hosts'], 1)
            self.assertEqual(metrics['heartbeat_hosts'], 0)
            async def check():
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
                    response = await client.get('/api/system')
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()['total_logs'], 1)
                    self.assertIn('queue_capacity', response.json()['receiver'])
                def close():
                    database._local.connection.close()
                    database._local.connection = None
                await asyncio.to_thread(close)
            asyncio.run(check())

    def test_multiple_host_exclusions_preserve_data_and_pagination(self):
        for host in ('noisy-a', 'noisy-b', 'quiet-a', 'quiet-b', "quote'host"):
            database.insert_log(host, 'app', 'INFO', 'message')
        rows, total = database.get_logs(exclude_hosts=['noisy-a','noisy-b',"quote'host"], sort_by='host', sort_order='asc', per_page=1)
        self.assertEqual(total, 2)
        self.assertEqual(rows[0]['host'], 'quiet-a')
        page, _ = database.get_logs(exclude_hosts=['noisy-a','noisy-b',"quote'host"], sort_by='host', sort_order='asc', per_page=1, page=2)
        self.assertEqual(page[0]['host'], 'quiet-b')
        self.assertEqual(database.get_logs()[1], 5)
        import asyncio
        import httpx
        import main
        async def check():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
                params = [('exclude_host','noisy-a'),('exclude_host','noisy-b'),('sort_by','host'),('sort_order','asc')]
                response = await client.get('/api/logs',params=params)
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.json()['total'],3)
                exported = await client.get('/api/logs/export',params=params+[('format','json')])
                self.assertEqual([row['host'] for row in exported.json()], ['quiet-a','quiet-b',"quote'host"])
        asyncio.run(check())

    def test_host_names_preserve_identity_and_apply_to_old_logs(self):
        database.insert_log('192.0.2.1', 'app', 'INFO', 'old message')
        database.set_host_alias('192.0.2.1', 'QNAP NAS')
        rows, total = database.get_logs(search='QNAP')
        self.assertEqual(total, 1)
        self.assertEqual(rows[0]['host'], '192.0.2.1')
        self.assertEqual(rows[0]['display_host'], 'QNAP NAS')
        self.assertEqual(database.get_filter_options()['host_labels']['192.0.2.1'], 'QNAP NAS')
        self.assertEqual(database.get_logs(host='192.0.2.1')[1], 1)
        self.assertEqual(database.get_logs(exclude_hosts=['192.0.2.1'])[1], 0)
        self.assertEqual(database.get_dashboard_summary()['hosts'][0]['display_host'], 'QNAP NAS')
        self.assertEqual(database.insert_log('192.0.2.1','app','INFO','new')['display_host'], 'QNAP NAS')
        database.set_host_alias('192.0.2.1', '')
        self.assertEqual(database.get_logs()[0][0]['display_host'], '192.0.2.1')

    def test_host_name_api_persists_and_validates(self):
        import asyncio
        import httpx
        import main
        async def check():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
                response = await client.put('/api/hosts/name',json={'host':'192.0.2.2','name':'Switch Büro'})
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.json()['display_host'],'Switch Büro')
                invalid = await client.put('/api/hosts/name',json={'host':'192.0.2.2','name':'a'*81})
                self.assertEqual(invalid.status_code,422)
                invalid = await client.put('/api/hosts/name',json={'host':'192.0.2.2','name':'line\nbreak'})
                self.assertEqual(invalid.status_code,422)
            database._local.connection.close()
            database._local.connection = None
            database.init_db()
            self.assertEqual(database.get_host_aliases()['192.0.2.2'],'Switch Büro')
        asyncio.run(check())

    def test_version_patch_follows_revision_and_fallback(self):
        with patch.object(version, 'get_revision_number', return_value=15), patch.object(version, 'get_git_revision', return_value='abc'):
            info = version.get_version_info()
            self.assertEqual(info['version'], '1.0.15')
            self.assertIn('Rev. 15', info['display'])


if __name__ == '__main__':
    unittest.main()
