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
            def close():
                database._local.connection.close()
                database._local.connection = None
            await asyncio.to_thread(close)
        asyncio.run(check())

    def test_version_patch_follows_revision_and_fallback(self):
        with patch.object(version, 'get_revision_number', return_value=15), patch.object(version, 'get_git_revision', return_value='abc'):
            info = version.get_version_info()
            self.assertEqual(info['version'], '1.0.15')
            self.assertIn('Rev. 15', info['display'])


if __name__ == '__main__':
    unittest.main()
