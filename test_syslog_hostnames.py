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
                self.assertIn('192.0.2.6', hosts)
                self.assertNotIn('pvemini2', hosts)
                self.assertNotIn('other-node', hosts)
                self.assertEqual(database.get_logs(host='192.0.2.6')[1], 2)
                self.assertEqual(conn.execute('SELECT received_at FROM logs WHERE id=?', (old['id'],)).fetchone()[0], received)
                self.assertEqual(database.get_host_aliases()['192.0.2.192'], 'UniFi Controller')
                self.assertEqual(database.get_stats()['total'], 4)
        finally:
            if database._local.connection:
                database._local.connection.close()
            database._local.connection = previous

class SourceResolutionTests(unittest.TestCase):
    def test_hostname_less_ccu_tags_are_not_hostnames(self):
        for tag in ['ReGaHss', 'rfd', 'multimac', 'kernel', 'monit[6266]', 'sshd-session[31250]']:
            entry = parse_syslog_message(f'<134>Oct 31 08:41:56 {tag}: Info: hello', '192.0.2.22')
            self.assertEqual(entry['host'], '192.0.2.22')
            self.assertEqual(entry['app'], tag.split('[')[0])
            self.assertEqual(entry['message'], 'Info: hello')

    def test_configured_source_and_historical_repair_preserve_original(self):
        import json
        import host_resolution
        previous = getattr(database._local, 'connection', None)
        database._local.connection = None
        try:
            with tempfile.TemporaryDirectory() as directory, patch.object(config, 'DATABASE_PATH', os.path.join(directory, 'db.sqlite')):
                database.init_db()
                raw = '<134>Oct 31 08:41:56 ReGaHss: Info: hello'
                database.insert_log('ReGaHss:', 'Info', 'INFO', 'hello', raw=raw)
                database.set_setting('syslog_source_names', json.dumps({'192.0.2.22':'CCU3'}))
                database.set_setting('syslog_legacy_host_names', json.dumps({'ReGaHss:':'CCU3'}))
                database.init_db()
                # A configured display name must not invent a missing historic IP.
                self.assertEqual(database.get_logs(host='ReGaHss:')[1], 1)
                with patch.object(host_resolution, 'resolve_ip', return_value='dns.example'):
                    entry = host_resolution.identify_source(parse_syslog_message(raw,'192.0.2.22'), '192.0.2.22')
                    self.assertEqual(entry['host'], '192.0.2.22')
                    self.assertEqual(database.get_host_aliases()['192.0.2.22'], 'CCU3')
                    explicit_ip = host_resolution.identify_source(parse_syslog_message('<14>Oct 31 08:41:56 192.0.2.22 app: hello','192.0.2.9'), '192.0.2.9')
                    self.assertEqual(explicit_ip['host'], '192.0.2.9')
                    self.assertEqual(explicit_ip['metadata']['reported_host'], '192.0.2.22')
                    named = host_resolution.identify_source(parse_syslog_message('<14>Oct 31 08:41:56 origin app: hello','192.0.2.22'), '192.0.2.22')
                    self.assertEqual(named['host'], '192.0.2.22')
                    self.assertEqual(named['metadata']['reported_host'], 'origin')
                    database.insert_log(entry['host'], entry['app'], entry['level'], entry['message'], metadata=entry['metadata'])
                    self.assertEqual(database.get_logs(host='192.0.2.22')[1], 1)
                # Migration uses recorded source_ip only and preserves original payload.
                old = database.insert_log('old-header', 'app', 'INFO', 'old', raw='original')
                with database.get_db_connection() as conn:
                    conn.execute('UPDATE logs SET metadata=? WHERE id=?', (json.dumps({'source_ip':'192.0.2.8'}),old['id']))
                    conn.execute("DELETE FROM settings WHERE key='migration_transport_source_v2'")
                database.init_db()
                logs, count = database.get_logs(host='192.0.2.8')
                self.assertEqual(count, 1)
                self.assertEqual(logs[0]['raw'], 'original')
                self.assertEqual(logs[0]['metadata']['reported_host'], 'old-header')
                self.assertEqual(database.get_logs(host='ReGaHss:')[1], 1)
        finally:
            if database._local.connection:
                database._local.connection.close()
            database._local.connection = previous


    def test_dns_cache_and_completion_after_timeout_are_bounded(self):
        import host_resolution as resolution
        from concurrent.futures import Future
        with resolution._lock:
            resolution._cache.clear(); resolution._pending.clear()
        future = Future()
        with patch.object(resolution._pool, 'submit', return_value=future) as submit:
            self.assertEqual(resolution.resolve_ip('192.0.2.55'), '192.0.2.55')
            future.set_result(('host.example', [], ['192.0.2.55']))
            self.assertEqual(resolution.resolve_ip('192.0.2.55'), 'host.example')
            self.assertEqual(submit.call_count, 1)
            self.assertNotIn('192.0.2.55', resolution._pending)
        failed = Future(); failed.set_exception(OSError('no PTR record'))
        with patch.object(resolution._pool, 'submit', return_value=failed):
            self.assertEqual(resolution.resolve_ip('192.0.2.56'), '192.0.2.56')
        with resolution._lock:
            resolution._cache.clear(); resolution._pending.clear()


if __name__ == '__main__':
    unittest.main()
