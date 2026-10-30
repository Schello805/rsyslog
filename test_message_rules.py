"""Device message rules: INFO matching, persistent per-channel throttling and failure retry."""
import asyncio
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

_tmp = tempfile.TemporaryDirectory()
os.environ['DATABASE_PATH'] = os.path.join(_tmp.name, 'rules.db')
import database
from config import config
import alerter
from custom_alerts import matches, validate_rules


class MessageRuleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = getattr(database._local,'connection',None)
        database._local.connection = None
        self.override = patch.object(config,'DATABASE_PATH',os.path.join(self.temp.name,'db.sqlite'))
        self.override.start();database.init_db()
        self.rule = dict(id='updates',name='Updates verfügbar',enabled=True,host='',app='',
            contains=['updates available','updates verfügbar'],excludes=['no updates available','keine updates verfügbar'],cooldown_seconds=3600)
        database.save_all_settings(dict(alert_rules=[self.rule],alert_min_level='ERROR',telegram_enabled=True,
            telegram_bot_token='test-token',telegram_chat_id='test-chat',ntfy_enabled=True,ntfy_topic='test-topic'))
        self.log = dict(host='nas',app='updater',level='INFO',message='2 updates available',timestamp='2026-10-30T00:00:00Z')

    def tearDown(self):
        if database._local.connection:database._local.connection.close()
        database._local.connection=self.previous;self.override.stop();self.temp.cleanup()

    def test_literal_terms_exclusions_and_scope(self):
        self.assertTrue(matches(self.rule,self.log))
        self.assertFalse(matches(self.rule,dict(self.log,message='No updates available')))
        self.assertFalse(matches(self.rule,dict(self.log,message='Keine Updates verfügbar')))
        self.assertFalse(matches(dict(self.rule,host='other'),self.log))
        self.assertFalse(matches(dict(self.rule,app='other'),self.log))
        self.assertFalse(matches(dict(self.rule,enabled=False),self.log))

    def test_info_rule_sends_once_per_channel_and_survives_reconnect(self):
        async def check():
            with patch.object(alerter,'send_telegram_alert',new=AsyncMock(return_value=True)) as telegram,patch.object(alerter,'send_ntfy_alert',new=AsyncMock(return_value=True)) as ntfy:
                await alerter.process_alert(self.log)
                database._local.connection.close();database._local.connection=None
                await alerter.process_alert(self.log)
                self.assertEqual(telegram.await_count,1);self.assertEqual(ntfy.await_count,1)
                self.assertIn('[Updates verfügbar]',telegram.await_args.args[0]['message'])
                await alerter.process_alert(dict(self.log,host='other'))
                self.assertEqual(telegram.await_count,2)
                await alerter.process_alert(dict(self.log,app='other'))
                self.assertEqual(telegram.await_count,3)
        asyncio.run(check())

    def test_failed_channel_retries_without_duplicating_successful_one(self):
        async def check():
            with patch.object(alerter,'send_telegram_alert',new=AsyncMock(return_value=True)) as telegram,patch.object(alerter,'send_ntfy_alert',new=AsyncMock(side_effect=[False,True])) as ntfy:
                await alerter.process_alert(self.log)
                await alerter.process_alert(self.log)
                self.assertEqual(telegram.await_count,1);self.assertEqual(ntfy.await_count,2)
        asyncio.run(check())

    def test_ignored_hosts_and_disabled_rules_do_not_send_info(self):
        async def check():
            with patch.object(alerter,'send_telegram_alert',new=AsyncMock(return_value=True)) as telegram:
                database.save_all_settings({'alert_ignore_hosts':'nas'})
                await alerter.process_alert(self.log)
                database.save_all_settings({'alert_ignore_hosts':'','alert_rules':[dict(self.rule,enabled=False)]})
                await alerter.process_alert(self.log)
                telegram.assert_not_awaited()
        asyncio.run(check())

    def test_nonmatching_error_still_uses_standard_alarm(self):
        async def check():
            with patch.object(alerter,'send_telegram_alert',new=AsyncMock(return_value=True)) as telegram,patch.object(alerter,'send_ntfy_alert',new=AsyncMock(return_value=True)):
                await alerter.process_alert(dict(self.log,host='baseline-test',level='ERROR',message='disk failure'))
                self.assertEqual(telegram.await_count,1)
                self.assertEqual(telegram.await_args.kwargs['log']['message'],'disk failure')
        asyncio.run(check())

    def test_persistent_cooldown_expires_at_boundary(self):
        key=('rule','host','app','ntfy')
        self.assertTrue(database.claim_rule_notification(key,60,1000))
        self.assertFalse(database.claim_rule_notification(key,60,1059))
        self.assertTrue(database.claim_rule_notification(key,60,1060))

    def test_rule_validation_and_persistence(self):
        saved=database.get_all_settings()['alert_rules']
        self.assertEqual(saved[0]['name'],'Updates verfügbar')
        for invalid in ([dict(self.rule,contains=[])],[self.rule,self.rule],[dict(self.rule,cooldown_seconds=0)]):
            with self.assertRaises(ValueError):validate_rules(invalid)
        with self.assertRaises(ValueError):database.save_all_settings({'alert_rules':[dict(self.rule,contains=[])]})
        self.assertEqual(database.get_all_settings()['alert_rules'][0]['id'],'updates')

    def test_first_matching_rule_prevents_duplicate_notifications(self):
        database.save_all_settings({'alert_rules':[self.rule,dict(self.rule,id='second')]})
        async def check():
            with patch.object(alerter,'send_telegram_alert',new=AsyncMock(return_value=True)) as telegram,patch.object(alerter,'send_ntfy_alert',new=AsyncMock(return_value=True)):
                await alerter.process_alert(dict(self.log,level='ERROR'))
                self.assertEqual(telegram.await_count,1)
        asyncio.run(check())


if __name__=='__main__':unittest.main()
