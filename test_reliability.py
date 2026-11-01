"""Isolated reliability regression checks; never use the live database."""
import asyncio
import importlib.util
import os
import tempfile
import time
import unittest
from pathlib import Path

_tmp = tempfile.TemporaryDirectory()
os.environ['DATABASE_PATH'] = os.path.join(_tmp.name, 'test.db')
import database
from config import config
# Explicit assignment also isolates tests when another test imported config first.
config.DATABASE_PATH = os.environ['DATABASE_PATH']
import main
from syslog_server import SyslogServerManager

spec = importlib.util.spec_from_file_location('resource_monitor', 'scripts/resource-monitor.py')
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        database.init_db()
        database.clear_all_logs()
        with database.get_db_connection() as conn:
            conn.execute('DELETE FROM monitored_hosts')

    def test_cleanup_commits_and_preserves_recent(self):
        database.insert_log('old', 'test', 'INFO', 'old', timestamp='2000-01-01T00:00:00Z')
        database.insert_log('new', 'test', 'INFO', 'new')
        self.assertEqual(database.delete_logs_older_than(7), 1)
        self.assertEqual(database.get_stats()['total'], 1)
        self.assertEqual(database.clear_all_logs(), 1)
        self.assertEqual(database.get_stats()['total'], 0)

    def test_offline_and_recovery_only_once(self):
        self.assertFalse(database.touch_monitored_host('192.0.2.1'))
        with database.get_db_connection() as conn:
            conn.execute('UPDATE monitored_hosts SET last_seen=?', (time.time()-400,))
        self.assertEqual(database.mark_offline_hosts(), ['192.0.2.1'])
        self.assertEqual(database.mark_offline_hosts(), [])
        self.assertTrue(database.touch_monitored_host('192.0.2.1'))
        self.assertFalse(database.touch_monitored_host('192.0.2.1'))

    def test_old_heartbeat_alarm_becomes_information(self):
        database.insert_log('192.0.2.1', 'host-monitor', 'ERROR', 'ALARM: Seit 5 Minuten kein Heartbeat')
        database.insert_log('192.0.2.1', 'backup', 'ERROR', 'Backup failed')
        with database.get_db_connection() as conn:
            conn.execute("DELETE FROM settings WHERE key='migration_quiet_availability_v1'")
        database.init_db()
        logs, _ = database.get_logs(app='host-monitor')
        self.assertEqual(logs[0]['level'], 'INFO')
        with database.get_db_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM incidents WHERE app='host-monitor'").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM incidents WHERE app='backup'").fetchone()[0], 1)

    def test_sustained_alarm_repeat_and_recovery(self):
        state, event = monitor.transition({}, 95, 90, 0, 600, 1800)
        self.assertIsNone(event)
        state, event = monitor.transition(state, 95, 90, 600, 600, 1800)
        self.assertEqual(event, 'warning')
        state, event = monitor.transition(state, 95, 90, 660, 600, 1800)
        self.assertIsNone(event)
        state, event = monitor.transition(state, 95, 90, 2400, 600, 1800)
        self.assertEqual(event, 'warning')
        state, event = monitor.transition(state, 50, 90, 2460, 600, 1800)
        self.assertEqual(event, 'recovery')

    def test_cgroup_uses_parent_quota_cpuset_and_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child = root / 'guest'
            child.mkdir()
            membership = root / 'membership'
            membership.write_text('0::/guest\n')
            (root / 'cpu.max').write_text('50000 100000')
            (root / 'memory.max').write_text('1000')
            (child / 'cpu.max').write_text('max 100000')
            (child / 'cpuset.cpus.effective').write_text('0-3,6')
            (child / 'cpu.stat').write_text('usage_usec 123456\n')
            (child / 'memory.current').write_text('900')
            (child / 'memory.stat').write_text('inactive_file 100\n')
            usage, capacity, ram = monitor.cgroup_metrics(root, membership)
            self.assertEqual(usage, 123456)
            self.assertEqual(capacity, 0.5)
            self.assertEqual(ram, 80)

    def test_heartbeat_round_trip_and_api(self):
        import httpx
        from syslog_parser import parse_syslog_message
        import monitoring
        parsed = parse_syslog_message('<14>Oct 30 18:30:00 node01 resource-monitor: HEARTBEAT', client_ip='192.0.2.1')
        self.assertEqual(parsed['app'], 'resource-monitor')
        parsed['host'] = '192.0.2.1'
        parsed['metadata'] = {'source_ip':'192.0.2.1'}
        async def check():
            await monitoring.observe_log(parsed)
            transport = httpx.ASGITransport(app=main.app)
            async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
                response = await client.get('/api/hosts')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['hosts'][0]['host'], '192.0.2.1')
                response = await client.post('/api/logs/clear')
                self.assertEqual(response.status_code, 200)
            def close_worker_connection():
                connection = getattr(database._local, 'connection', None)
                if connection:
                    connection.close()
                    database._local.connection = None
            await asyncio.to_thread(close_worker_connection)
        asyncio.run(check())

    def test_udp_queue_is_bounded(self):
        manager = SyslogServerManager()
        for _ in range(2001):
            manager.enqueue('test', '127.0.0.1')
        self.assertEqual(manager.queue.qsize(), 2000)
        self.assertEqual(manager.dropped, 1)

    def test_slow_browser_does_not_block_others(self):
        async def check():
            slow, fast = object(), object()
            main._active_websockets[slow] = asyncio.Queue(maxsize=1)
            main._active_websockets[fast] = asyncio.Queue(maxsize=2)
            await main.websocket_broadcaster({'id': 1})
            await main.websocket_broadcaster({'id': 2})
            self.assertNotIn(slow, main._active_websockets)
            self.assertEqual(main._active_websockets[fast].qsize(), 2)
            main._active_websockets.clear()
        asyncio.run(check())


if __name__ == '__main__':
    unittest.main()
