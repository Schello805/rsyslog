import unittest
from message_severity import effective_level


class MessageSeverityTests(unittest.TestCase):
    def test_explicit_application_prefix_overrides_info(self):
        message = '2026/11/06 18:58:49 ERR [path 192.168.1.111] [RTSP source] dial tcp 192.168.1.111:554: connect: no route to host'
        self.assertEqual(effective_level('INFO', message), 'ERROR')
        self.assertEqual(effective_level('CRITICAL', message), 'CRITICAL')
        self.assertEqual(effective_level('INFO', '2026-11-06T18:58:49Z [WARN] issue'), 'WARN')

    def test_normal_service_messages_and_cef_are_preserved(self):
        for message in ('Starting rsyslog-resource-monitor.service - CPU, RAM and disk warnings through syslog...', 'Finished rsyslog-resource-monitor.service - CPU, RAM and disk warnings through syslog.', 'rsyslog-resource-monitor.service: Deactivated successfully.', 'Info: ERR text quoted by application', 'ERROR_COUNT=0'):
            self.assertEqual(effective_level('INFO', message), 'INFO')
        self.assertEqual(effective_level('INFO', 'ERR problem', {'event_id':'512'}), 'INFO')
