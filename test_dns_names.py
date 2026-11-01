"""Reported names remain labels; only matching forward DNS becomes confirmed DNS."""
import os
import tempfile
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace
import dns.resolver
import database
import host_resolution as resolution
from config import config
from metrics_cache import invalidate


class DNSNameTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.previous=getattr(database._local,'connection',None)
        database._local.connection=None
        self.override=patch.object(config,'DATABASE_PATH',os.path.join(self.temp.name,'test.db'))
        self.override.start(); database.init_db(); invalidate()

    def tearDown(self):
        database._local.connection.close(); database._local.connection=self.previous
        invalidate(); self.override.stop(); self.temp.cleanup()

    def test_reported_name_fallback_dns_and_manual_precedence(self):
        ip='192.0.2.153'
        database.insert_log(ip,'app','INFO','hello',metadata={'source_ip':ip,'reported_host':'HeartPet'})
        host=database.get_dashboard_summary()['hosts'][0]
        self.assertEqual((host['display_host'],host['name_source'],host['source_ip']),('HeartPet','reported',ip))
        self.assertIn('gemeldet',database.get_filter_options()['host_groups'][0]['options'][0]['label'])
        database.remember_source_dns(ip,'heartpet.schello')
        host=database.get_dashboard_summary()['hosts'][0]
        self.assertEqual((host['display_host'],host['name_source']),('heartpet.schello','dns'))
        database.set_host_alias(ip,'Manueller Name')
        host=database.get_dashboard_summary()['hosts'][0]
        self.assertEqual((host['display_host'],host['name_source']),('Manueller Name','manual'))
        self.assertEqual(database.get_logs()[0][0]['host'],ip)

    def test_ambiguous_reported_names_and_unproven_legacy_names_are_not_used(self):
        for name in ['first','second']:
            database.insert_log('192.0.2.1','app','INFO','hello',metadata={'source_ip':'192.0.2.1','reported_host':name})
        database.insert_log('192.0.2.2','app','INFO','hello',metadata={'reported_host':'missing-source'})
        old=database.insert_log('192.0.2.3','app','INFO','hello',metadata={'source_ip':'192.0.2.4','reported_host':'wrong-source'})
        with database.get_db_connection() as conn:
            conn.execute('UPDATE logs SET host=? WHERE id=?',('192.0.2.3',old['id']))
        self.assertEqual(database.get_reported_source_names(),{})
        self.assertEqual(database.get_dashboard_summary()['known_hosts'],3)

    def test_cef_affected_device_never_becomes_controller_name(self):
        database.insert_log('192.0.2.192','app','INFO','hello',metadata={'source_ip':'192.0.2.192','reported_host':'affected-ap','format':'CEF','reporting_host':'controller'})
        self.assertEqual(database.get_reported_source_names(),{'192.0.2.192':'controller'})

    def test_custom_dns_forward_confirmation_requires_matching_ip(self):
        database.save_all_settings({'dns_servers':'192.0.2.100','dns_search_domain':'schello'})
        resolver=Mock(); resolver.resolve_address.side_effect=dns.resolver.NXDOMAIN()
        class Answer(list): canonical_name='heartpet.schello.'
        resolver.resolve.return_value=Answer([SimpleNamespace(address='192.0.2.153')])
        with patch.object(resolution.dns.resolver,'Resolver',return_value=resolver):
            self.assertEqual(resolution._lookup_name('192.0.2.153',('HeartPet',))[0],'heartpet.schello')
            self.assertEqual(resolver.nameservers,['192.0.2.100'])
            resolver.resolve.assert_called_with('HeartPet.schello','A')
            resolver.resolve.return_value=Answer([SimpleNamespace(address='192.0.2.99')])
            self.assertEqual(resolution._lookup_name('192.0.2.153',('HeartPet',))[0],'192.0.2.153')

    def test_failed_worker_and_stale_results_do_not_poison_cache(self):
        from concurrent.futures import Future
        address='192.0.2.250'
        resolution.reset_dns_cache()
        failed=Future(); failed.set_exception(RuntimeError('failed worker'))
        with patch.object(resolution._pool,'submit',return_value=failed):
            self.assertEqual(resolution.resolve_ip(address),address)
        self.assertNotIn(address,resolution._pending)
        resolution.reset_dns_cache()
        old=Future()
        with patch.object(resolution._pool,'submit',return_value=old):
            self.assertEqual(resolution.resolve_ip(address),address)
        resolution.reset_dns_cache()
        old.set_result(('old-dns.example',[],[address]))
        fresh=Future(); fresh.set_result(('new-dns.example',[],[address]))
        with patch.object(resolution._pool,'submit',return_value=fresh):
            self.assertEqual(resolution.resolve_ip(address),'new-dns.example')
        resolution.reset_dns_cache()

    def test_dns_settings_validation_and_no_partial_save(self):
        for invalid in ['not-a-server','192.0.2.1,192.0.2.2,192.0.2.3,192.0.2.4']:
            with self.assertRaises(ValueError):database.save_all_settings({'dns_servers':invalid})
        with self.assertRaises(ValueError):database.save_all_settings({'dns_search_domain':'not a domain'})
        database.save_all_settings({'dns_servers':'192.0.2.100','dns_search_domain':'schello'})
        self.assertEqual(database.get_all_settings()['dns_servers'],'192.0.2.100')


if __name__ == '__main__':unittest.main()
