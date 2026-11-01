import unittest
from unittest.mock import AsyncMock, patch
import asyncio
from availability_policy import is_availability_notice
import monitoring


class AvailabilityTests(unittest.TestCase):
    def test_only_explicit_device_state_is_filtered(self):
        for text in ('Device Offline', 'Office AP is offline.', 'Host server is unreachable', 'Client ist nicht erreichbar'):
            # An arbitrary named subject is deliberately insufficient evidence.
            expected = text != 'Office AP is offline.'
            self.assertEqual(is_availability_notice('syslog', text), expected, text)
        for text in ('Backup failed: host unreachable', 'Database offline', 'Connection refused', 'Disk offline', 'CPU critical'):
            self.assertFalse(is_availability_notice('syslog', text), text)
        self.assertTrue(is_availability_notice('host-monitor', 'ALARM: Seit 5 Minuten kein Heartbeat'))

    def test_recovery_only_updates_status(self):
        with patch('database.touch_monitored_host', return_value=True) as touch:
            asyncio.run(monitoring.observe_log({'app':'resource-monitor','message':'HEARTBEAT','host':'192.0.2.1'}))
            touch.assert_called_once_with('192.0.2.1')
