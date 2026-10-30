"""Validate guest selection and command generation without accessing Proxmox."""
import base64
import contextlib
import importlib.util
import io
import json
import os
import tempfile
from pathlib import Path
import subprocess
import tarfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('pve_install', 'scripts/install-pve-guests.py')
pve = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pve)


class PveInstallTests(unittest.TestCase):
    def test_payload_is_local_bundle_with_explicit_receiver(self):
        script = pve.payload('192.168.1.158', 5514, True)
        self.assertIn('export RSYSLOG_SERVER=192.168.1.158', script)
        encoded = script.split("printf '%s' '", 1)[1].split("'", 1)[0]
        with tarfile.open(fileobj=io.BytesIO(base64.b64decode(encoded)), mode='r:gz') as archive:
            self.assertEqual(set(archive.getnames()), {'install-monitor.sh', 'resource-monitor.py',
                'rsyslog-resource-monitor.service', 'rsyslog-resource-monitor.timer'})
        subprocess.run(['bash', '-n'], input=script, text=True, check=True)
        self.assertIn('--forward-only', pve.payload('server.local', 5514, False))

    def test_dry_run_does_not_install_and_reports_skips(self):
        commands = []
        def fake_run(command, **kwargs):
            commands.append(command)
            if command[0] == 'pvesh':
                return json.dumps([{'vmid': 101, 'status': 'running', 'name': 'linux'},
                                   {'vmid': 102, 'status': 'stopped', 'name': 'stopped'}]
                    if command[2].endswith('/lxc') else [{'vmid': 201, 'status': 'running', 'name': 'vm'}])
            if command[0] == 'qm':
                raise RuntimeError('guest agent unavailable')
            return ''
        output = io.StringIO()
        with patch.object(pve, 'run', side_effect=fake_run), patch.object(pve.os, 'geteuid', return_value=0), \
             patch('sys.argv', ['install', '--server', '192.168.1.158', '--dry-run']), contextlib.redirect_stdout(output):
            self.assertEqual(pve.main(), 1)
        self.assertIn('BEREIT: lxc 101', output.getvalue())
        self.assertIn('gestoppt oder Vorlage', output.getvalue())
        self.assertIn('Guest Agent nicht erreichbar', output.getvalue())
        self.assertFalse(any('exec-status' in command or 'bash' in command for command in commands))

    def test_forward_only_installer_needs_no_terminal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'rsyslog.d'
            config.mkdir()
            commands = root / 'bin'
            commands.mkdir()
            for name in ('apt-get', 'systemctl', 'rsyslogd'):
                path = commands / name
                path.write_text('#!/bin/sh\nexit 0\n')
                path.chmod(0o755)
            script = Path('scripts/install-monitor.sh').read_text()
            script = script.replace('[[ $EUID == 0 ]]', '[[ 1 == 1 ]]')
            script = script.replace('[[ -d /run/systemd/system ]]', '[[ 1 == 1 ]]')
            script = script.replace('/etc/rsyslog.d', str(config))
            isolated = root / 'install.sh'
            isolated.write_text(script)
            result = subprocess.run(['bash', str(isolated), '--non-interactive', '--forward-only'],
                env=dict(os.environ, PATH=f'{commands}:/usr/bin:/bin', RSYSLOG_SERVER='192.168.1.158', RSYSLOG_PORT='5514'),
                stdin=subprocess.DEVNULL, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((config / '99-resource-monitor-remote.conf').read_text(), '*.* @192.168.1.158:5514\n')

    def test_vm_guest_exit_failure_is_not_success(self):
        with patch.object(pve, 'run', side_effect=[json.dumps({'pid': 17}),
              json.dumps({'exited': 1, 'exitcode': 1, 'err-data': 'apt failed'})]):
            with self.assertRaisesRegex(RuntimeError, 'apt failed'):
                pve.vm_execute(201, 'test')

    def test_vm_guest_success(self):
        with patch.object(pve, 'run', side_effect=[json.dumps({'pid': 17}),
              json.dumps({'exited': 1, 'exitcode': 0, 'out-data': 'installed'})]):
            self.assertEqual(pve.vm_execute(201, 'test'), 'installed')


if __name__ == '__main__':
    unittest.main()
