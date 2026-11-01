"""DEBUG is discarded by default at all ingestion boundaries, never retroactively."""
import asyncio
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import database
from config import config
import syslog_server


class DebugIngestionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = getattr(database._local, 'connection', None)
        database._local.connection = None
        self.override = patch.object(config, 'DATABASE_PATH', os.path.join(self.temp.name, 'db.sqlite'))
        self.override.start()
        database.init_db()

    def tearDown(self):
        database._local.connection.close()
        database._local.connection = self.previous
        self.override.stop()
        self.temp.cleanup()

    def test_syslog_default_discards_debug_and_keeps_other_levels(self):
        self.assertTrue(database.get_all_settings()['discard_debug'])
        self.assertIsNone(syslog_server.handle_incoming_log_sync('<15>Oct 31 08:00:00 host app: verbose', '192.0.2.1'))
        for pri in (14, 13, 12, 11, 10):
            self.assertIsNotNone(syslog_server.handle_incoming_log_sync(f'<{pri}>Oct 31 08:00:00 host app: event', '192.0.2.1'))
        self.assertEqual(database.get_stats()['total'], 5)

    def test_option_is_immediate_persistent_and_does_not_delete_old_debug(self):
        database.save_all_settings({'discard_debug': False})
        self.assertIsNotNone(syslog_server.handle_incoming_log_sync('<15>Oct 31 08:00:00 host app: verbose', '192.0.2.1'))
        database._local.connection.close(); database._local.connection = None
        self.assertFalse(database.get_all_settings()['discard_debug'])
        database.save_all_settings({'discard_debug': True})
        self.assertIsNone(syslog_server.handle_incoming_log_sync('<15>Oct 31 08:00:00 host app: verbose', '192.0.2.1'))
        self.assertEqual(database.get_logs(level='DEBUG')[1], 1)

    def test_dropped_syslog_does_not_alert_broadcast_or_observe(self):
        async def check():
            with patch('log_writer.writer.submit', new=AsyncMock(return_value=None)), patch.object(syslog_server.alerter, 'process_alert', new=AsyncMock()) as alert, patch.object(syslog_server, 'broadcast_log', new=AsyncMock()) as broadcast, patch('monitoring.observe_log', new=AsyncMock()) as observe:
                self.assertIsNone(await syslog_server.handle_incoming_log('debug', '192.0.2.1'))
                alert.assert_not_awaited(); broadcast.assert_not_awaited(); observe.assert_not_awaited()
        asyncio.run(check())

    def test_http_json_and_raw_drop_debug_and_honor_setting(self):
        import httpx
        import main
        async def check():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
                with patch.object(main, 'process_alert', new=AsyncMock()) as alert, patch.object(main, 'broadcast_log', new=AsyncMock()) as broadcast:
                    for kwargs in ({'json': {'host':'host','app':'app','level':'DEBUG','message':'verbose'}}, {'content':'<15>Oct 31 08:00:00 host app: verbose'}):
                        response = await client.post('/api/logs', **kwargs)
                        self.assertEqual(response.status_code, 202)
                        self.assertTrue(response.json()['discarded'])
                    alert.assert_not_awaited(); broadcast.assert_not_awaited()
                    self.assertEqual(database.get_stats()['total'], 0)
                    response = await client.post('/api/settings', json={'discard_debug': False})
                    self.assertFalse(response.json()['settings']['discard_debug'])
                    response = await client.post('/api/logs', json={'level':'DEBUG','message':'diagnostic'})
                    self.assertEqual(response.status_code, 201)
                    self.assertEqual(response.json()['log']['level'], 'DEBUG')
                    self.assertEqual(database.get_stats()['total'], 1)
        asyncio.run(check())

    def test_cef_effective_severity_takes_precedence_over_outer_debug(self):
        raw = '<15>Oct 31 08:00:00 gateway unifi: CEF:0|Ubiquiti|UniFi Network|10|513|Hardware Failure|8|UNIFIhost=Gateway msg=hardware failure'
        saved = syslog_server.handle_incoming_log_sync(raw, '192.0.2.1')
        self.assertIsNotNone(saved)
        self.assertEqual(saved['level'], 'ERROR')


if __name__ == '__main__':
    unittest.main()
