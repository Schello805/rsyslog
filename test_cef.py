"""UniFi CEF parsing and persistence regression tests."""
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from syslog_parser import parse_syslog_message


class CefTests(unittest.TestCase):
    def event(self, severity='8', extension='UNIFIcategory=System UNIFIsubCategory=Devices UNIFIhost=Gateway UNIFIdeviceName=Office AP msg=Office AP is offline.'):
        return f'CEF:0|Ubiquiti|UniFi Network|10.0|512|Device Offline|{severity}|{extension}'

    def test_affected_host_event_and_severity(self):
        raw = self.event()
        log = parse_syslog_message(raw, '192.0.2.1')
        self.assertEqual(log['host'], 'Office AP')
        self.assertEqual(log['app'], 'UniFi / System / Devices')
        self.assertEqual(log['level'], 'ERROR')
        self.assertIn('Device Offline [512]', log['message'])
        self.assertEqual(log['metadata']['reporting_host'], 'Gateway')
        self.assertEqual(log['raw'], raw)

    def test_severity_boundaries_override_outer_syslog_level(self):
        for severity, level in [('0','INFO'),('3','INFO'),('4','WARN'),('6','WARN'),('7','ERROR'),('8','ERROR'),('9','CRITICAL'),('10','CRITICAL'),('Very-High','CRITICAL')]:
            with self.subTest(severity=severity):
                log = parse_syslog_message('<134>Oct 30 18:00:00 gateway unifi: ' + self.event(severity))
                self.assertEqual(log['level'], level)

    def test_transports(self):
        for prefix in ('', '<134>', '<134>Oct 30 18:00:00 gateway ',
                       '<134>Oct 30 18:00:00 gateway unifi: ',
                       '<134>1 2026-10-30T18:00:00Z gateway unifi - - - ',
                       '<134>1 2026-10-30T18:00:00Z gateway unifi - - [test a="b"] ',
                       '<134>1 2026-10-30T18:00:00Z gateway unifi - - [one a="b"][two x="y"] '):
            with self.subTest(prefix=prefix):
                self.assertEqual(parse_syslog_message(prefix + self.event())['metadata']['event_id'], '512')

    def test_escaped_header_extension_and_multiline(self):
        raw = r'CEF:0|Ubiquiti|UniFi Network|10|12|Rule \| blocked|4|UNIFIhost=Gateway West msg=Rule name\=test path=C:\\logs\nsecond line'
        log = parse_syslog_message(raw)
        self.assertEqual(log['metadata']['event_name'], 'Rule | blocked')
        self.assertEqual(log['host'], 'Gateway West')
        self.assertEqual(log['metadata']['extensions']['msg'], 'Rule name=test')
        self.assertEqual(log['metadata']['extensions']['path'], 'C:\\logs\nsecond line')

    def test_invalid_or_other_vendor_is_preserved(self):
        for raw in ('CEF:0|Ubiquiti|incomplete', self.event().replace('Ubiquiti','Other'), 'ordinary message with ' + self.event()):
            self.assertNotIn('metadata', parse_syslog_message(raw))
        log = parse_syslog_message('<11>Oct 30 18:00:00 host app: '+self.event('99'))
        self.assertEqual(log['level'], 'ERROR')

    def test_database_upgrade_and_structured_json_ingestion(self):
        import database
        from config import config
        previous = getattr(database._local, 'connection', None)
        database._local.connection = None
        try:
            with tempfile.TemporaryDirectory() as directory:
                db = os.path.join(directory, 'old.db')
                with sqlite3.connect(db) as connection:
                    connection.execute('CREATE TABLE logs(id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, host TEXT NOT NULL, app TEXT NOT NULL, level TEXT NOT NULL, facility TEXT, message TEXT NOT NULL, raw TEXT)')
                with patch.object(config, 'DATABASE_PATH', db):
                    database.init_db()
                    database.init_db()
                    saved = database.insert_log('webhook', 'webhook', 'INFO', self.event())
                    self.assertEqual(saved['level'], 'ERROR')
                    logs, total = database.get_logs(app='UniFi / System / Devices', search='512')
                    self.assertEqual(total, 1)
                    self.assertEqual(logs[0]['metadata']['event_name'], 'Device Offline')
                    self.assertEqual(logs[0]['raw'], self.event())
                    import asyncio
                    import httpx
                    import main
                    from unittest.mock import AsyncMock
                    async def verify_api():
                        with patch.object(main, 'process_alert', new_callable=AsyncMock) as alert, patch.object(main, 'broadcast_log', new_callable=AsyncMock):
                            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
                                for body in ({'content': self.event(), 'headers': {'content-type':'text/plain'}}, {'json': {'message': self.event()}}):
                                    response = await client.post('/api/logs', **body)
                                    self.assertEqual(response.status_code, 202)
                                    alert.assert_not_awaited()
                    asyncio.run(verify_api())
                    database._local.connection.close()
                    database._local.connection = None
        finally:
            database._local.connection = previous


if __name__ == '__main__':
    unittest.main()
