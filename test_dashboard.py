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

    def test_same_device_hostname_is_not_a_second_device_or_offline_alarm(self):
        import json
        import system_metrics
        ip = '192.0.2.22'
        database.insert_log(ip,'app','INFO','new',metadata={'source_ip':ip})
        historic = database.insert_log('old-name','app','WARN','recorded old source',raw='unchanged payload')
        database.insert_log('old-name','app','WARN','unknown old source')
        database.remember_source_dns(ip,'old-name')
        database.touch_monitored_host('old-name')
        database.touch_monitored_host(ip)
        with database.get_db_connection() as conn:
            conn.execute('UPDATE logs SET metadata=? WHERE id=?',(json.dumps({'source_ip':ip}),historic['id']))
            conn.execute("UPDATE monitored_hosts SET last_seen=? WHERE host='old-name'",(time.time()-600,))
            conn.execute("DELETE FROM settings WHERE key='migration_transport_source_v2'")
            conn.execute("INSERT OR REPLACE INTO settings VALUES('migration_transport_source_v1','done')")
        database.init_db()
        summary = database.get_dashboard_summary()
        self.assertEqual([host['host'] for host in summary['hosts']],[ip])
        self.assertEqual(summary['known_hosts'],1)
        self.assertEqual(summary['heartbeat_hosts'],1)
        self.assertEqual(summary['offline_hosts'],0)
        self.assertEqual([host['host'] for host in summary['unassigned_hosts']],['old-name'])
        self.assertEqual(database.get_logs(host=ip)[1],2)
        self.assertEqual(database.get_logs(host='old-name')[1],1)
        self.assertEqual(database.get_logs()[1],3)
        self.assertEqual(database.get_stats()['active_hosts'],1)
        self.assertEqual(system_metrics.collect_system_metrics()['known_hosts'],1)
        self.assertEqual(database.mark_offline_hosts(),[])
        self.assertEqual(database.get_monitored_hosts()[0]['host'],ip)
        raw = database.get_db_connection().execute('SELECT raw FROM logs WHERE id=?',(historic['id'],)).fetchone()[0]
        self.assertEqual(raw,'unchanged payload')
        database.init_db()
        self.assertEqual(database.get_logs()[1],3)

    def test_host_filter_uses_uniform_ip_labels_and_separate_legacy_group(self):
        for host in ['192.0.2.10','192.0.2.6','192.0.2.22','old-device']:
            database.insert_log(host,'app','INFO','hello')
        database.remember_source_dns('192.0.2.6','same-name.lan')
        database.remember_source_dns('192.0.2.10','same-name.lan')
        database.set_host_alias('192.0.2.10','Manuell')
        groups = database.get_filter_options()['host_groups']
        self.assertEqual([item['value'] for item in groups[0]['options']], ['192.0.2.6','192.0.2.10','192.0.2.22'])
        self.assertEqual(groups[0]['options'][0]['label'], '192.0.2.6 (same-name.lan)')
        self.assertEqual(groups[0]['options'][1]['label'], '192.0.2.10 (Manuell)')
        self.assertEqual(groups[0]['options'][2]['label'], '192.0.2.22 (DNS nicht aufgelöst)')
        self.assertEqual(groups[1]['options'], [{'value':'old-device','label':'old-device · Quell-IP nicht gespeichert'}])
        self.assertEqual(database.get_logs()[1],4)

    def test_dns_refresh_updates_labels_without_new_messages_or_merging_hosts(self):
        import host_resolution
        for host in ['192.0.2.6','192.0.2.10','old-device']:
            database.insert_log(host,'app','INFO','hello')
        database.set_host_alias('192.0.2.6','Mein Gerät')
        with patch.object(host_resolution,'_refresh_cursor',0), patch.object(host_resolution,'resolve_ip',return_value='shared-name.lan') as resolve:
            self.assertEqual(host_resolution.refresh_known_sources(batch_size=1),1)
            self.assertEqual(host_resolution.refresh_known_sources(batch_size=1),1)
            self.assertEqual(resolve.call_count,2)
            self.assertEqual({call.args[0] for call in resolve.call_args_list},{'192.0.2.6','192.0.2.10'})
        aliases = database.get_host_aliases()
        self.assertEqual(aliases['192.0.2.6'],'Mein Gerät')
        self.assertEqual(aliases['192.0.2.10'],'shared-name.lan')
        self.assertEqual(set(database.get_filter_options()['hosts']),{'192.0.2.6','192.0.2.10','old-device'})
        self.assertEqual(database.get_logs()[1],3)

    def test_device_identity_keeps_manual_name_dns_and_source_separate(self):
        database.insert_log('192.0.2.1','app','INFO','hello',metadata={'source_ip':'192.0.2.1'})
        database.remember_source_dns('192.0.2.1','device.lan')
        database.set_host_alias('192.0.2.1','Mein Gerät')
        host = database.get_dashboard_summary()['hosts'][0]
        self.assertEqual((host['host'],host['source_ip'],host['dns_name'],host['display_host']),('192.0.2.1','192.0.2.1','device.lan','Mein Gerät'))

    def test_http_identity_uses_peer_ip_and_cef_still_sets_severity(self):
        import asyncio
        import httpx
        import main
        async def check():
            transport = httpx.ASGITransport(app=main.app, client=('192.0.2.42', 1234))
            async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
                response = await client.post('/api/logs', json={'host':'forged-name','message':'hello'})
                self.assertEqual(response.status_code, 201)
                self.assertEqual(response.json()['log']['host'], '192.0.2.42')
                self.assertEqual(response.json()['log']['metadata']['reported_host'], 'forged-name')
                response = await client.post('/api/logs', json={'host':'forged-name','message':'CEF:0|Ubiquiti|UniFi|1|42|Failure|9|src=192.0.2.9'})
                self.assertEqual(response.status_code, 201)
                self.assertEqual(response.json()['log']['host'], '192.0.2.42')
                self.assertEqual(response.json()['log']['level'], 'CRITICAL')
        asyncio.run(check())

    def test_process_tags_are_separate_from_devices_without_losing_logs(self):
        tags = ['kernel:', 'monit[6266]:', 'sshd-session[31250]:', 'homematic:']
        for host in tags + ['CCU3', '192.168.1.22', '2001:db8::1']:
            database.insert_log(host, 'app', 'ERROR', 'historic')
        summary = database.get_dashboard_summary()
        self.assertEqual(summary['known_hosts'], 2)
        self.assertEqual(summary['recent_hosts'], 2)
        self.assertEqual({host['host'] for host in summary['unassigned_hosts']}, set(tags + ['CCU3']))
        self.assertEqual(summary['errors_24h'], 7)
        self.assertEqual(database.get_logs(host='kernel:')[1], 1)
        self.assertEqual(database.get_logs()[1], 7)
        filters = database.get_filter_options()
        self.assertIn('kernel:', filters['hosts'])
        self.assertEqual(filters['host_labels']['kernel:'], 'Nicht zugeordnet · kernel:')

    def test_receipt_index_and_recent_metrics_include_legacy_rows(self):
        database.insert_log('legacy', 'app', 'INFO', 'legacy')
        database.insert_log('old', 'app', 'INFO', 'old')
        database.insert_log('monitor', 'resource-monitor', 'INFO', 'HEARTBEAT')
        conn = database.get_db_connection()
        with conn:
            conn.execute("UPDATE logs SET received_at='' WHERE host='legacy'")
            conn.execute("UPDATE logs SET received_at='2000-01-01' WHERE host='old'")
        metrics = database.get_ingestion_metrics()
        self.assertEqual(metrics['total_logs'], 3)
        self.assertEqual(metrics['logs_last_minute'], 2)
        self.assertEqual(metrics['heartbeats_last_minute'], 1)
        self.assertEqual(metrics['events_last_minute'], 1)
        self.assertEqual(metrics['logs_per_minute_5m'], 0.4)
        plan = conn.execute(f"EXPLAIN QUERY PLAN SELECT COUNT(*) FROM logs WHERE {database.RECEIPT_SQL} >= julianday('now','-5 minutes')").fetchall()
        self.assertTrue(any('SEARCH logs USING' in row['detail'] and 'idx_logs_receipt_time' in row['detail'] for row in plan))

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
        database.insert_log('192.0.2.1', 'storage', 'WARN', 'low space')
        result = database.get_dashboard_summary()
        self.assertEqual(result['known_hosts'], 1)
        self.assertEqual(result['heartbeat_hosts'], 0)
        self.assertEqual(result['hosts'][0]['status'], 'syslog')
        self.assertEqual(result['recent_hosts'], 1)
        self.assertEqual(result['warnings_24h'], 1)

    def test_receipt_time_controls_windows_and_heartbeat_is_not_an_event(self):
        database.insert_log('192.0.2.2', 'service', 'ERROR', 'clock is wrong', timestamp='2000-01-01T00:00:00Z')
        database.insert_log('192.0.2.2', 'resource-monitor', 'INFO', 'HEARTBEAT')
        database.touch_monitored_host('192.0.2.2')
        database.touch_monitored_host('192.0.2.3')
        with database.get_db_connection() as connection:
            connection.execute('UPDATE monitored_hosts SET last_seen=? WHERE host=?', (time.time()-600, '192.0.2.3'))
        result = database.get_dashboard_summary()
        self.assertEqual(result['errors_24h'], 1)
        self.assertEqual(result['heartbeat_hosts'], 2)
        self.assertEqual(result['offline_hosts'], 1)
        self.assertEqual(len(result['latest']), 1)
        self.assertEqual(result['hosts'][0]['host'], '192.0.2.3')

    def test_critical_events_remain_visible_before_newer_warnings(self):
        for level in ('CRITICAL', 'ALERT', 'EMERGENCY'):
            database.insert_log('192.0.2.4', 'app', level, level)
        for number in range(8):
            database.insert_log('192.0.2.4', 'app', 'WARN', str(number))
        result = database.get_dashboard_summary()
        self.assertEqual([item['level'] for item in result['attention'][:3]], ['EMERGENCY', 'ALERT', 'CRITICAL'])
        self.assertEqual(len(result['attention']), 6)
        self.assertEqual(result['latest'][0]['message'], '7')

    def test_timestamp_sort_preserves_order_without_startup_index_build(self):
        database.insert_log('192.0.2.4', 'app', 'INFO', 'older', timestamp='2026-01-01T10:00:00+00:00')
        database.insert_log('192.0.2.4', 'app', 'INFO', 'newer', timestamp='2026-01-01T12:00:00+00:00')
        database.insert_log('192.0.2.5', 'app', 'INFO', 'other', timestamp='2026-01-01T13:00:00+00:00')
        for direction, expected in [('desc', ['newer', 'older']), ('asc', ['older', 'newer'])]:
            logs, total = database.get_logs(host='192.0.2.4', sort_order=direction)
            self.assertEqual(total, 2)
            self.assertEqual([log['message'] for log in logs], expected)
        conn = database.get_db_connection()
        indexes = {row['name'] for row in conn.execute('PRAGMA index_list(logs)')}
        self.assertNotIn('idx_logs_sort_time', indexes)
        self.assertNotIn('idx_logs_host_sort_time', indexes)

    def test_slow_log_query_does_not_block_event_loop(self):
        import asyncio
        import time
        import httpx
        import main
        from unittest.mock import patch
        def slow_query(**kwargs):
            time.sleep(0.15)
            return [], 0
        async def check():
            with patch.object(main, 'get_logs', side_effect=slow_query):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
                    request = asyncio.create_task(client.get('/api/logs'))
                    await asyncio.sleep(0.03)
                    self.assertFalse(request.done(), 'Log SQL blocked the event loop')
                    self.assertEqual((await request).status_code, 200)
        asyncio.run(check())

    def test_fast_received_order_ignores_device_clock_and_needs_no_sort(self):
        database.insert_log('192.0.2.4', 'app', 'INFO', 'first', timestamp='2099-01-01T00:00:00Z')
        database.insert_log('192.0.2.4', 'app', 'INFO', 'second', timestamp='2000-01-01T00:00:00Z')
        logs, total = database.get_logs(sort_by='received')
        self.assertEqual([row['message'] for row in logs], ['second', 'first'])
        self.assertEqual(total, 2)
        for where in ('', "WHERE host='192.0.2.4'", "WHERE level='INFO'"):
            plan = ' '.join(str(tuple(row)) for row in database.get_db_connection().execute(f'EXPLAIN QUERY PLAN SELECT id FROM logs {where} ORDER BY id DESC,id DESC LIMIT 50'))
            self.assertNotIn('TEMP B-TREE', plan)
        import asyncio
        import httpx
        import main
        async def check():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
                result = await client.get('/api/logs')
                self.assertEqual(result.json()['logs'][0]['message'], 'second')
        asyncio.run(check())

    def test_old_events_do_not_count_as_current_attention(self):
        database.insert_log('192.0.2.4', 'app', 'ERROR', '192.0.2.4')
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
        database.insert_log('192.0.2.5', 'network', 'WARNING', 'port issue')
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
        database.insert_log('192.0.2.1', 'storage', 'INFO', 'test')
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
