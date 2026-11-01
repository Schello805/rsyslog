import asyncio
import json
import os
import tempfile
import unittest
from unittest.mock import patch
import httpx
import database
import host_management as hosts
from config import config
from fastapi import FastAPI
from metrics_cache import invalidate


class HostRemovalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.previous=getattr(database._local,'connection',None)
        database._local.connection=None
        self.override=patch.object(config,'DATABASE_PATH',os.path.join(self.temp.name,'test.db'))
        self.override.start(); database.init_db(); invalidate()
        for host in ('192.0.2.1','192.0.2.10'):
            database.insert_log(host,'app','WARN','disk full',metadata={'source_ip':host})
            database.touch_monitored_host(host); database.set_host_alias(host,'Name '+host)
            database.remember_source_dns(host,'device.example')
        database.save_all_settings({'alert_rules':[{'id':'one','name':'one','host':'192.0.2.1','contains':['disk']},{'id':'other','name':'other','host':'192.0.2.10','contains':['disk']}], 'syslog_source_names':json.dumps({'192.0.2.1':'one','192.0.2.10':'other'})})
        self.app=FastAPI();self.app.include_router(hosts.router)

    def tearDown(self):
        database._local.connection.close();database._local.connection=self.previous
        invalidate();self.override.stop();self.temp.cleanup()

    def test_preview_is_read_only_and_command_is_for_client_not_remote_execution(self):
        with patch.object(hosts,'get_local_ip_addresses',return_value=['192.0.2.158']):
            info=hosts.removal_info('192.0.2.1','192.0.2.158')
        self.assertEqual(info['logs'],1);self.assertEqual(info['incidents'],1);self.assertEqual(info['host_rules'],1)
        self.assertIn('uninstall-client.sh',info['uninstall_command'])
        self.assertIn('--server 192.0.2.158',info['uninstall_command'])
        self.assertEqual(database.get_logs()[1],2)

    def test_delete_is_exact_and_new_messages_can_add_device_again(self):
        self.assertEqual(hosts.remove_host('192.0.2.1'),1)
        self.assertEqual(database.get_logs()[1],1)
        self.assertEqual(database.get_logs(host='192.0.2.10')[1],1)
        self.assertEqual([h['host'] for h in database.get_monitored_hosts()],['192.0.2.10'])
        self.assertEqual([rule['id'] for rule in database.get_all_settings()['alert_rules']],['other'])
        self.assertEqual(json.loads(database.get_setting('syslog_source_names')),{'192.0.2.10':'other'})
        self.assertEqual(database.get_db_connection().execute('SELECT COUNT(*) FROM incidents').fetchone()[0],1)
        self.assertEqual(hosts.remove_host('192.0.2.1'),0)
        database.insert_log('192.0.2.1','app','INFO','new message')
        self.assertEqual(database.get_dashboard_summary()['known_hosts'],2)

    def test_delete_rolls_back_if_a_later_step_fails(self):
        database.set_setting('alert_rules','not-json')
        with self.assertRaises(ValueError):hosts.remove_host('192.0.2.1')
        self.assertEqual(database.get_logs()[1],2)
        self.assertEqual(len(database.get_monitored_hosts()),2)

    def test_running_analysis_prevents_cache_from_being_recreated_after_delete(self):
        import ai_analysis
        async def check():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://test') as client:
                async with ai_analysis._busy:
                    response=await client.post('/api/hosts/remove',json={'host':'192.0.2.1','confirm_host':'192.0.2.1'},headers={'X-RSyslog-Action':'remove-host'})
                self.assertEqual(response.status_code,409)
                self.assertEqual(database.get_logs()[1],2)
        asyncio.run(check())

    def test_post_requires_same_origin_custom_header_and_typed_ip(self):
        async def check():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://test') as client:
                body={'host':'192.0.2.1','confirm_host':'192.0.2.1'}
                self.assertEqual((await client.post('/api/hosts/remove',json=body)).status_code,403)
                headers={'X-RSyslog-Action':'remove-host'}
                self.assertEqual((await client.post('/api/hosts/remove',json=body,headers={**headers,'Origin':'https://other'})).status_code,403)
                self.assertEqual((await client.post('/api/hosts/remove',json={**body,'confirm_host':'wrong'},headers=headers)).status_code,422)
                self.assertEqual(database.get_logs()[1],2)
                response=await client.post('/api/hosts/remove',json=body,headers=headers)
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.json()['deleted_logs'],1)
        asyncio.run(check())


if __name__ == '__main__':unittest.main()
