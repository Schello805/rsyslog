"""No root service or network used: update checks, auth, queue and runner state."""
import asyncio
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch
import httpx
from fastapi import HTTPException
import updates


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'request').mkdir()
        self.control = patch.object(updates, 'CONTROL', self.root)
        self.control.start()
        updates._cache.clear()
        self.current = {'revision': 'a'*7, 'revision_number': 16, 'version':'1.0.16'}

    def tearDown(self):
        self.control.stop(); self.temp.cleanup()

    def test_new_revision_and_cache(self):
        calls = []
        def handler(request):
            calls.append(request.url.path)
            return httpx.Response(200, json={'sha':'b'*40} if '/commits/' in request.url.path else {'status':'ahead','ahead_by':2})
        client = httpx.AsyncClient
        async def check():
            with patch.object(updates, 'get_version_info', return_value=self.current), patch.object(updates.httpx, 'AsyncClient', side_effect=lambda **kw: client(transport=httpx.MockTransport(handler), **kw)):
                first = await updates.check_updates()
                second = await updates.check_updates()
            self.assertTrue(first['available'])
            self.assertEqual(second['latest_version'], '1.0.18')
            self.assertEqual(len(calls), 2)
        asyncio.run(check())

    def test_github_failure_does_not_claim_current(self):
        client = httpx.AsyncClient
        async def check():
            with patch.object(updates, 'get_version_info', return_value=self.current), patch.object(updates.httpx, 'AsyncClient', side_effect=lambda **kw: client(transport=httpx.MockTransport(lambda r:httpx.Response(403)), **kw)):
                result = await updates.check_updates()
            self.assertEqual(result['check_state'], 'unknown')
            self.assertFalse(result['available'])
        asyncio.run(check())

    def test_authorized_request_is_atomic_and_duplicates_rejected(self):
        async def check():
            with patch.object(updates, 'check_updates', new=AsyncMock(return_value={'available':True})):
                result = await updates.request_update()
                self.assertEqual(result['state'], 'queued')
                self.assertTrue((self.root / 'request/start').exists())
                with self.assertRaises(HTTPException) as duplicate:
                    await updates.request_update()
                self.assertEqual(duplicate.exception.status_code, 409)
        asyncio.run(check())

    def test_unsupported_and_no_new_version(self):
        async def check():
            with patch.object(updates, 'check_updates', new=AsyncMock(return_value={'available':False})):
                with self.assertRaises(HTTPException) as current:
                    await updates.request_update()
                self.assertEqual(current.exception.status_code, 409)
            (self.root / 'request').rmdir()
            with self.assertRaises(HTTPException) as missing:
                await updates.request_update()
            self.assertEqual(missing.exception.status_code, 503)
        asyncio.run(check())

    def test_progress_labels_are_fixed_and_do_not_echo_file_contents(self):
        (self.root/'status.json').write_text(json.dumps({'state':'running'}))
        (self.root/'progress').write_text('backup')
        with patch.object(updates,'_service_state',return_value='activating'):
            job=updates.job_status()
            self.assertEqual(job['phase_label'],'Sichern')
            self.assertEqual(job['step'],4)
            (self.root/'progress').write_text('<script>bad</script>')
            self.assertNotIn('phase_label',updates.job_status())

    def test_legacy_running_status_detects_stopped_service(self):
        (self.root/'status.json').write_text(json.dumps({'state':'running','updated_at':1}))
        with patch.object(updates, '_service_state', return_value='inactive'):
            self.assertEqual(updates.job_status()['state'], 'failed')
        with patch.object(updates, '_service_state', return_value='activating'):
            self.assertEqual(updates.job_status()['state'], 'running')
        with patch.object(updates, '_service_state', return_value=None):
            self.assertEqual(updates.job_status()['state'], 'unknown')

    def test_running_process_identity_detects_dead_or_reused_pid(self):
        (self.root/'status.json').write_text(json.dumps({'state':'running','pid':123,'process_start':'42'}))
        real_path = updates.Path
        class ProcPath:
            def __init__(self, value): self.value = value
            def is_dir(self): return True
            def read_text(self): return '123 (worker name) ' + ' '.join(['S']*19 + ['99'])
        with patch.object(updates, 'Path', side_effect=lambda value: ProcPath(value) if str(value).startswith('/proc') else real_path(value)):
            self.assertEqual(updates.job_status()['state'], 'failed')

    def test_templates_get_revisioned_assets_and_no_store(self):
        import main
        path = self.root / 'template.html'
        path.write_text('<script src="/assets/update-footer.js"></script><link href="/assets/theme.css?v=old"><img src="/assets/branding/logo.png">')
        with patch.object(main, 'get_version_info', return_value=self.current):
            response = main.render_template(path)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        text = response.body.decode()
        self.assertIn('/assets/update-footer.js?v=aaaaaaa', text)
        self.assertIn('/assets/theme.css?v=aaaaaaa', text)
        self.assertNotIn('v=old', text)

    def test_api_denies_cross_origin_and_never_returns_key(self):
        import main
        async def check():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
                denied = await client.post('/api/updates/start', headers={'origin':'http://other','x-rsyslog-update':'1'})
                self.assertEqual(denied.status_code, 403)
                cross_site = await client.post('/api/updates/start', headers={'x-rsyslog-update':'1','sec-fetch-site':'cross-site'})
                self.assertEqual(cross_site.status_code, 403)
                with patch.object(updates, 'get_version_info', return_value={'revision':'unknown'}):
                    info = await client.get('/api/updates')
                self.assertNotIn('test-only-key', info.text)
                missing = await client.post('/api/updates/start')
                self.assertEqual(missing.status_code, 403)
                with patch.object(updates, 'check_updates', new=AsyncMock(return_value={'available':True})):
                    queued = await client.post('/api/updates/start', headers={'origin':'http://test','x-rsyslog-update':'1'})
                self.assertEqual(queued.status_code, 202)
                self.assertTrue((self.root / 'request/start').exists())
                (self.root / 'request/start').unlink()
                status = await client.get('/api/updates/status')
                self.assertEqual(status.json()['state'], 'idle')
        asyncio.run(check())

    def test_helper_setup_is_idempotent_without_key(self):
        script = Path('scripts/setup-web-updates.sh').read_text()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'scripts'
            source.mkdir()
            shutil.copy('scripts/web-update-runner.py', source / 'web-update-runner.py')
            script = script.replace('[[ $EUID == 0 ]]', '[[ 1 == 1 ]]')
            script = script.replace('-o root -g rsyslog-web ', '')
            script = script.replace('chown root:rsyslog-web ', 'true ')
            for path in ('/usr/local/lib/rsyslog-web', '/var/lib/rsyslog-web-update', '/etc/rsyslog-web-update.key', '/etc/systemd/system'):
                script = script.replace(path, str(root) + path)
            unit_dir = root / 'etc/systemd/system'
            unit_dir.mkdir(parents=True)
            fake_bin = root / 'bin'
            fake_bin.mkdir()
            command = fake_bin / 'systemctl'
            command.write_text('#!/bin/sh\nexit 0\n'); command.chmod(0o755)
            (fake_bin / 'python3').symlink_to(sys.executable)
            setup = source / 'setup.sh'; setup.write_text(script)
            env = dict(os.environ, PATH=f'{fake_bin}:/usr/bin:/bin')
            subprocess.run(['bash',str(setup)],env=env,check=True,capture_output=True)
            subprocess.run(['bash',str(setup)],env=env,check=True,capture_output=True)
            self.assertFalse((root / 'etc/rsyslog-web-update.key').exists())
            self.assertEqual((root/'var/lib/rsyslog-web-update/request').stat().st_mode & 0o777,0o770)
            self.assertIn('ReadWritePaths=',(unit_dir/'rsyslog-web.service.d/updates.conf').read_text())
            self.assertIn('Type=oneshot',(unit_dir/'rsyslog-web-update.service').read_text())
            self.assertIn('ExecStopPost=',(unit_dir/'rsyslog-web-update.service').read_text())

    def test_finalizer_clears_abandoned_running_state_and_preserves_result(self):
        spec = importlib.util.spec_from_file_location('runner','scripts/web-update-runner.py')
        runner = importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)
        with patch.object(runner, 'CONTROL', self.root):
            runner.status('running', 'preparing')
            self.assertEqual(runner.finalize(), 0)
            self.assertEqual(json.loads((self.root/'status.json').read_text())['state'], 'failed')
            runner.status('succeeded', 'done')
            runner.finalize()
            self.assertEqual(json.loads((self.root/'status.json').read_text())['state'], 'succeeded')

    def test_runner_uses_fixed_command_and_persists_result(self):
        spec = importlib.util.spec_from_file_location('runner','scripts/web-update-runner.py')
        runner = importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)
        for code, state in [(0,'succeeded'),(1,'failed')]:
            (self.root / 'request/start').touch()
            with patch.object(runner,'CONTROL',self.root), patch.object(runner.subprocess,'run',return_value=Mock(returncode=code)) as command:
                self.assertEqual(runner.main(), code)
            self.assertEqual(command.call_args.args[0], ['/bin/bash','/opt/rsyslog-web/scripts/update-debian.sh'])
            self.assertEqual(json.loads((self.root/'status.json').read_text())['state'], state)
            self.assertFalse((self.root/'request/start').exists())


if __name__ == '__main__':
    unittest.main()
