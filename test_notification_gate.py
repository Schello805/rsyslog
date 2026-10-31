import asyncio
import unittest
from unittest.mock import AsyncMock, patch
import notification_gate as gate
import alerter


class NotificationTests(unittest.TestCase):
    def setUp(self):
        gate.reset()
        alerter._incident_timestamps.clear()
        self.log = dict(host='192.0.2.1', app='disk', level='WARN', message='space low')

    def test_duplicates_budget_and_escalation_are_per_host_and_channel(self):
        with patch.object(gate.time, 'monotonic', return_value=1000):
            self.assertIsNotNone(gate.reserve(self.log, 'ntfy', 30))
            self.assertIsNone(gate.reserve(self.log, 'ntfy', 30))
            self.assertIsNotNone(gate.reserve(self.log, 'telegram', 30))
            for i in range(4):
                self.assertIsNotNone(gate.reserve(dict(self.log, message=str(i)), 'ntfy', 30))
            self.assertIsNone(gate.reserve(dict(self.log, message='different'), 'ntfy', 30))
            self.assertIsNotNone(gate.reserve(dict(self.log, level='CRITICAL'), 'ntfy', 30))
            self.assertIsNone(gate.reserve(dict(self.log, level='CRITICAL'), 'ntfy', 30))
            self.assertIsNotNone(gate.reserve(dict(self.log, host='192.0.2.2'), 'ntfy', 30))
        with patch.object(gate.time, 'monotonic', return_value=1061):
            self.assertIsNotNone(gate.reserve(self.log, 'ntfy', 30))

    def test_group_escalation_bypasses_budget_even_after_another_critical(self):
        with patch.object(gate.time, 'monotonic', return_value=1000):
            gate.reserve(dict(self.log,message='another',level='CRITICAL'),'ntfy',300)
            gate.reserve(self.log,'ntfy',300)
            for i in range(3): gate.reserve(dict(self.log,message=str(i)),'ntfy',300)
        with patch.object(gate.time, 'monotonic', return_value=1001):
            self.assertIsNotNone(gate.reserve(dict(self.log,level='CRITICAL'),'ntfy',300))
        with patch.object(gate.time, 'monotonic', return_value=1061):
            critical = dict(self.log,level='ALERT')
            self.assertTrue(gate.is_escalation(critical,300))
            self.assertIsNotNone(gate.reserve(critical,'ntfy',300))
            self.assertIsNotNone(gate.reserve(dict(self.log,level='EMERGENCY'),'ntfy',300))

    def test_failed_channel_is_retryable_and_success_not_duplicated(self):
        async def check():
            fail = AsyncMock(side_effect=[False, True])
            self.assertFalse(await gate.send(self.log, 'ntfy', fail, 30))
            self.assertTrue(await gate.send(self.log, 'ntfy', fail, 30))
            self.assertFalse(await gate.send(self.log, 'ntfy', fail, 30))
            self.assertEqual(fail.await_count, 2)
        asyncio.run(check())

    def test_concurrent_duplicates_reserve_before_network_send(self):
        async def check():
            entered, release = asyncio.Event(), asyncio.Event()
            async def send():
                entered.set(); await release.wait(); return True
            task = asyncio.create_task(gate.send(self.log, 'ntfy', send, 30))
            await entered.wait()
            self.assertFalse(await gate.send(self.log, 'ntfy', AsyncMock(), 30))
            release.set(); self.assertTrue(await task)
        asyncio.run(check())

    def test_escalation_bypasses_occurrence_threshold(self):
        settings = dict(alert_min_level='WARN', alert_threshold_count=3, alert_threshold_window_minutes=5,
                        alert_throttle_seconds=30, ntfy_enabled=True, ntfy_topic='test', alert_rules=[])
        async def check():
            with patch.object(alerter.database, 'get_all_settings', return_value=settings), patch.object(alerter, 'send_ntfy_alert', new=AsyncMock(return_value=True)) as send:
                for _ in range(3): await alerter.process_alert(self.log)
                self.assertEqual(send.await_count, 1)
                await alerter.process_alert(dict(self.log, level='CRITICAL'))
                self.assertEqual(send.await_count, 2)
                await alerter.process_alert(dict(self.log, level='CRITICAL'))
                self.assertEqual(send.await_count, 2)
        asyncio.run(check())


if __name__ == '__main__': unittest.main()
