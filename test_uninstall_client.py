"""Exercise the shell script in a temporary filesystem, never against /etc."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class UninstallTests(unittest.TestCase):
    def run_uninstall(self, root, fail=False):
        root=Path(root);bindir=root/'bin';bindir.mkdir(exist_ok=True)
        (bindir/'systemctl').write_text('#!/bin/sh\necho "$*" >> "$TEST_SYSTEMCTL_LOG"\nexit 0\n')
        (bindir/'rsyslogd').write_text('#!/bin/sh\nexit "${FAIL_VALIDATE:-0}"\n')
        for path in bindir.iterdir():path.chmod(0o755)
        code=Path('scripts/uninstall-client.sh').read_text().replace('[[ $EUID == 0 ]]','[[ 1 == 1 ]]')
        for prefix in ('/etc/','/usr/local/','/var/lib/'):
            code=code.replace(prefix,str(root)+prefix)
        script=root/'uninstall.sh';script.write_text(code)
        env={**os.environ,'PATH':str(bindir)+':'+os.environ['PATH'],'TEST_SYSTEMCTL_LOG':str(root/'calls'),'FAIL_VALIDATE':'1' if fail else '0'}
        return subprocess.run(['bash',str(script),'--server','192.0.2.158','--port','5514'],env=env,capture_output=True,text=True)

    def fixture(self, root):
        root=Path(root)
        for name in ('etc/rsyslog.d','etc/systemd/system','usr/local/sbin','var/lib/rsyslog-resource-monitor'):
            (root/name).mkdir(parents=True)
        (root/'etc/rsyslog.d/99-resource-monitor-remote.conf').write_text('*.* @192.0.2.158:5514\n')
        (root/'etc/rsyslog.d/99-remote.conf').write_text('# keep comment\n*.* @@192.0.2.158:5514\n*.* @192.0.2.99:5514\n')
        (root/'etc/rsyslog.d/20-custom.conf').write_text('*.* @192.0.2.158:5514\n')
        for name in ('rsyslog-resource-monitor.timer','rsyslog-resource-monitor.service'):
            (root/'etc/systemd/system'/name).write_text('unit')
        (root/'usr/local/sbin/rsyslog-resource-monitor.py').write_text('monitor')
        (root/'etc/rsyslog-resource-monitor.json').write_text('{}')
        (root/'var/lib/rsyslog-resource-monitor/state.json').write_text('{}')

    def test_removes_only_matching_forwarding_and_monitor_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            self.fixture(directory);root=Path(directory)
            result=self.run_uninstall(root)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertFalse((root/'etc/rsyslog.d/99-resource-monitor-remote.conf').exists())
            self.assertEqual((root/'etc/rsyslog.d/99-remote.conf').read_text(),'# keep comment\n*.* @192.0.2.99:5514\n')
            self.assertTrue((root/'etc/rsyslog.d/20-custom.conf').exists())
            self.assertFalse((root/'usr/local/sbin/rsyslog-resource-monitor.py').exists())
            self.assertNotIn('disable --now rsyslog.service',(root/'calls').read_text())
            self.assertEqual(self.run_uninstall(root).returncode,0)

    def test_invalid_remaining_config_rolls_back_before_stopping_monitor(self):
        with tempfile.TemporaryDirectory() as directory:
            self.fixture(directory);root=Path(directory)
            result=self.run_uninstall(root,True)
            self.assertNotEqual(result.returncode,0)
            self.assertEqual((root/'etc/rsyslog.d/99-resource-monitor-remote.conf').read_text(),'*.* @192.0.2.158:5514\n')
            self.assertTrue((root/'usr/local/sbin/rsyslog-resource-monitor.py').exists())
            self.assertFalse((root/'calls').exists())


if __name__ == '__main__':unittest.main()
