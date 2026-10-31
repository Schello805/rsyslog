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
        self.key = self.root / 'secret.key'
        self.key.write_text('test-only-key')
        self.control = patch.object(updates, 'CONTROL', self.root)
        self.key_path = patch.object(updates, 'KEY_FILE', self.key)
        self.control.start(); self.key_path.start()
        updates._cache.clear()
        self.current = {'revision': 'a'*7, 'revision_number': 16, 'version':'1.0.16'}

    def tearDown(self):
        self.control.stop(); self.key_path.stop(); self.temp.cleanup()

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
            with self.assertRaises(HTTPException) as denied:
                await updates.request_update('wrong')
            self.assertEqual(denied.exception.status_code, 403)
            with patch.object(updates, 'check_updates', new=AsyncMock(return_value={'available':True})):
                result = await updates.request_update('test-only-key')
                self.assertEqual(result['state'], 'queued')
                self.assertTrue((self.root / 'request/start').exists())
                with self.assertRaises(HTTPException) as duplicate:
                    await updates.request_update('test-only-key')
                self.assertEqual(duplicate.exception.status_code, 409)
        asyncio.run(check())

    def test_unsupported_and_no_new_version(self):
        async def check():
            with patch.object(updates, 'check_updates', new=AsyncMock(return_value={'available':False})):
                with self.assertRaises(HTTPException) as current:
                    await updates.request_update('test-only-key')
                self.assertEqual(current.exception.status_code, 409)
            self.key.unlink()
            with self.assertRaises(HTTPException) as missing:
                await updates.request_update('test-only-key')
            self.assertEqual(missing.exception.status_code, 503)
        asyncio.run(check())

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
                denied = await client.post('/api/updates/start', headers={'origin':'http://other','x-update-key':'test-only-key'})
                self.assertEqual(denied.status_code, 403)
                with patch.object(updates, 'get_version_info', return_value={'revision':'unknown'}):
                    info = await client.get('/api/updates')
                self.assertNotIn('test-only-key', info.text)
                status = await client.get('/api/updates/status')
                self.assertEqual(status.json()['state'], 'idle')
        asyncio.run(check())

    def test_helper_setup_is_idempotent_and_key_is_not_world_readable(self):
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
            key = root / 'etc/rsyslog-web-update.key'
            first = key.read_text()
            subprocess.run(['bash',str(setup)],env=env,check=True,capture_output=True)
            self.assertEqual(key.read_text(),first)
            self.assertGreaterEqual(len(first.strip()),32)
            self.assertEqual(key.stat().st_mode & 0o777,0o640)
            self.assertIn('ReadWritePaths=',(unit_dir/'rsyslog-web.service.d/updates.conf').read_text())
            self.assertIn('Type=oneshot',(unit_dir/'rsyslog-web-update.service').read_text())

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
