"""Keep BSD hostnames when TAGs are unusual; repair historical IP fallbacks."""
import os
import tempfile
import unittest
from unittest.mock import patch

import database
from config import config
from syslog_parser import parse_syslog_message


class HostnameTests(unittest.TestCase):
    def test_parenthesized_systemd_tags_preserve_hostname(self):
        for name in ('systemd', 'sd-pam'):
            raw = f'<86>Oct 31 08:12:31 pbsmini ({name}): pam_unix: session closed'
            entry = parse_syslog_message(raw, '192.0.2.10')
            self.assertEqual(entry['host'], 'pbsmini')
            self.assertEqual(entry['app'], name)
            self.assertEqual(entry['message'], 'pam_unix: session closed')
            self.assertEqual(entry['raw'], raw)

    def test_truncated_tag_and_arbitrary_body_keep_header_host(self):
        raw = '<27>Oct 31 06:40:34 Homematic-analyzer systemd-networkd-wait-online[798 Timeout occurred'
        entry = parse_syslog_message(raw, '192.0.2.121')
        self.assertEqual(entry['host'], 'Homematic-analyzer')
        self.assertEqual(entry['app'], 'systemd-networkd-wait-online')
        self.assertEqual(entry['level'], 'ERROR')
        self.assertEqual(entry['message'], 'Timeout occurred')
        entry = parse_syslog_message('<14>Oct 31 08:12:31 server.example @unusual: body', '192.0.2.1')
        self.assertEqual(entry['host'], 'server.example')
        self.assertEqual(entry['message'], '@unusual: body')

    def test_unstructured_and_standard_messages_keep_previous_behavior(self):
        self.assertEqual(parse_syslog_message('raw text', '192.0.2.1')['host'], '192.0.2.1')
        self.assertEqual(parse_syslog_message('<14>raw text', '192.0.2.1')['host'], '192.0.2.1')
        entry = parse_syslog_message('<86>Oct 31 08:12:31 pve CRON[123]: session closed', '192.0.2.1')
        self.assertEqual((entry['host'], entry['app'], entry['message']), ('pve', 'CRON', 'session closed'))

    def test_startup_repair_is_precise_idempotent_and_preserves_raw(self):
        previous = getattr(database._local, 'connection', None)
        database._local.connection = None
        try:
            with tempfile.TemporaryDirectory() as directory, patch.object(config, 'DATABASE_PATH', os.path.join(directory, 'db.sqlite')):
                database.init_db()
                conn = database.get_db_connection()
                with conn:
                    conn.execute("DELETE FROM settings WHERE key='migration_syslog_hostname_v1'")
                raw = '<86>Oct 31 08:12:31 pvemini2 (systemd): session opened'
                old = database.insert_log('192.0.2.6', 'syslog', 'INFO', raw, raw=raw)
                # Same sender IP may forward for another machine. Repair each header independently.
                other = '<27>Oct 31 06:40:34 other-node systemd-networkd-wait-online[798 Timeout occurred'
                database.insert_log('192.0.2.6', 'syslog', 'ERROR', other, raw=other)
                database.insert_log('192.0.2.192', 'generic', 'INFO', 'plain device message')
                database.insert_log('192.0.2.20', 'syslog', 'INFO', '<14>plain message', raw='<14>plain message')
                database.set_host_alias('192.0.2.192', 'UniFi Controller')
                received = conn.execute('SELECT received_at FROM logs WHERE id=?', (old['id'],)).fetchone()[0]
                database.init_db()  # Performs upgrade automatically.
                hosts = database.get_filter_options()['hosts']
                self.assertIn('pvemini2', hosts)
                self.assertIn('other-node', hosts)
                self.assertNotIn('192.0.2.6', hosts)
                self.assertIn('192.0.2.192', hosts)
                self.assertIn('192.0.2.20', hosts)
                rows, total = database.get_logs(host='pvemini2')
                self.assertEqual(total, 1)
                self.assertEqual(rows[0]['raw'], raw)
                self.assertEqual(rows[0]['message'], 'session opened')
                self.assertEqual(database.get_logs(exclude_hosts=['pvemini2'])[1], 3)
                self.assertEqual(conn.execute('SELECT received_at FROM logs WHERE id=?', (old['id'],)).fetchone()[0], received)
                self.assertEqual(database.get_host_aliases()['192.0.2.192'], 'UniFi Controller')
                self.assertEqual(database.repair_legacy_syslog_hosts(), 0)
                self.assertEqual(database.get_stats()['total'], 4)
        finally:
            if database._local.connection:
                database._local.connection.close()
            database._local.connection = previous


if __name__ == '__main__':
    unittest.main()
