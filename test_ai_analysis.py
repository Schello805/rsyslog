"""Exercise credentials, filter snapshots and both provider protocols without API calls."""
import asyncio
import os
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

import httpx
import database
import ai_analysis as ai
from config import config
from fastapi import FastAPI


class AIAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = getattr(database._local, 'connection', None)
        database._local.connection = None
        self.override = patch.object(config, 'DATABASE_PATH', os.path.join(self.temp.name, 'test.db'))
        self.override.start()
        database.init_db()
        ai._snapshots.clear()
        self.app = FastAPI()
        self.app.include_router(ai.router)

    def tearDown(self):
        database._local.connection.close()
        database._local.connection = self.previous
        self.override.stop()
        self.temp.cleanup()

    def run_api(self, operation):
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://test') as client:
                await operation(client)
        asyncio.run(run())

    def test_write_only_keys_preserve_replace_delete_and_validation(self):
        async def check(client):
            body = {'openai': {'key': 'secret-one', 'model': 'test-model'}, 'gemini': {'key': 'secret-two', 'model': 'test-gemini'}}
            response = await client.post('/api/ai/settings', json=body)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('secret-', response.text)
            self.assertTrue(response.json()['openai']['configured'])
            body['openai']['key'] = ''
            body['gemini'] = {'delete': True}
            await client.post('/api/ai/settings', json=body)
            self.assertEqual(database.get_setting('ai_openai_key'), 'secret-one')
            self.assertEqual(database.get_setting('ai_gemini_key'), '')
            self.assertNotIn('ai_openai_key', database.get_all_settings())
            body['openai']['key'] = 'PRIVATE' * 200
            response = await client.post('/api/ai/settings', json=body)
            self.assertEqual(response.status_code, 400)
            self.assertNotIn('PRIVATE', response.text)
            response = await client.post('/api/ai/settings', json=body, headers={'Origin': 'https://other'})
            self.assertEqual(response.status_code, 403)
        self.run_api(check)

    def test_preview_filters_severity_exclusions_and_redaction(self):
        for host, level, message in [('nas', 'WARN', 'password=private token=abc Authorization: Bearer xyz'),
                                    ('noisy', 'ERROR', 'bad'), ('nas', 'INFO', 'fine'), ('nas', 'WARN', 'unrelated')]:
            database.insert_log(timestamp='2026-10-30T00:00:00Z', host=host, app='disk', level=level,
                                     facility='user', message=message, raw='raw-secret')
        async def check(client):
            response = await client.post('/api/ai/preview', json={'exclude_hosts': ['noisy'], 'search': 'password'})
            data = response.json()
            self.assertEqual(data['count'], 1)
            self.assertNotIn('private', data['preview'])
            self.assertNotIn('abc', data['preview'])
            self.assertNotIn('xyz', data['preview'])
            self.assertNotIn('raw-secret', data['preview'])
            self.assertEqual(ai._snapshots[data['token']]['payload'], data['preview'])
            response = await client.post('/api/ai/preview', json={'level': 'INFO'})
            self.assertIsNone(response.json()['token'])
        self.run_api(check)

    def test_preview_limits_expiry_and_no_external_call(self):
        for number in range(105):
            database.insert_log(host='nas', app='disk', level='WARN', message=f'{number}: ' + 'x' * 2500)
        async def check(client):
            with patch.object(ai.httpx.AsyncClient, 'post', AsyncMock()) as external:
                response = await client.request('POST', '/api/ai/preview', json={'host': 'nas', 'app': 'disk', 'hours': 24})
                external.assert_not_awaited()
            data = response.json()
            self.assertEqual(data['total'], 105)
            self.assertLessEqual(data['count'], 100)
            self.assertLess(len(data['preview']), 70000)
            ai._snapshots[data['token']]['created'] -= 601
            response = await client.post('/api/ai/analyze', json={'provider': 'openai', 'token': data['token']})
            self.assertEqual(response.status_code, 410)
            response = await client.post('/api/ai/preview', json={'host': 'other'})
            self.assertEqual(response.json()['count'], 0)
        self.run_api(check)

    def test_preview_groups_repetitions_but_keeps_counts_and_payload_exact(self):
        for _ in range(26):
            database.insert_log(host='nas', app='disk', level='WARN', message='disk full')
        database.insert_log(host='other', app='disk', level='WARN', message='disk full')
        async def check(client):
            import json
            response = await client.post('/api/ai/preview', json={})
            data = response.json()
            self.assertEqual(data['count'], 27)
            self.assertEqual(data['groups'], 2)
            self.assertEqual(sorted(item['occurrences'] for item in json.loads(data['preview'])), [1,26])
            self.assertEqual(ai._snapshots[data['token']]['payload'], data['preview'])
        self.run_api(check)

    def test_provider_payloads_and_plain_text_result(self):
        async def check(client):
            ai._snapshots['test'] = {'created': time.monotonic(), 'payload': 'preview-data', 'count': 1}
            for provider in ('openai', 'gemini'):
                database.set_setting(f'ai_{provider}_key', 'server-secret')
                database.set_setting(f'ai_{provider}_model', 'test-model')
                result = ({'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': '<script>example</script>'}]}]}
                          if provider == 'openai' else {'candidates': [{'content': {'parts': [{'text': '<script>example</script>'}]}, 'finishReason': 'MAX_TOKENS'}]})
                post = AsyncMock(return_value=httpx.Response(200, json=result))
                with patch.object(ai.httpx.AsyncClient, 'post', post):
                    # Call ASGI using request() to keep the external post mock isolated.
                    response = await client.request('POST', '/api/ai/analyze', json={'provider': provider, 'token': 'test'})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()['analysis'], '<script>example</script>')
                self.assertNotIn('server-secret', response.text)
                kwargs = post.call_args.kwargs
                if provider == 'openai':
                    self.assertFalse(kwargs['json']['store'])
                    self.assertEqual(kwargs['json']['input'], 'preview-data')
                else:
                    self.assertEqual(kwargs['headers']['x-goog-api-key'], 'server-secret')
                    self.assertEqual(kwargs['json']['contents'][0]['parts'][0]['text'], 'preview-data')
                    self.assertTrue(response.json()['incomplete'])
        self.run_api(check)

    def test_total_deadline_returns_error_and_releases_analysis_lock(self):
        async def check(client):
            ai._snapshots['test'] = {'created': time.monotonic(), 'payload': 'data', 'count': 1}
            database.set_setting('ai_openai_key', 'secret')
            database.set_setting('ai_openai_model', 'test-model')
            deadline = asyncio.timeout
            async def slow(*args, **kwargs):
                await asyncio.sleep(0.1)
            with patch.object(ai.asyncio, 'timeout', side_effect=lambda seconds: deadline(0.001)), patch.object(ai.httpx.AsyncClient, 'post', new=AsyncMock(side_effect=slow)):
                response = await client.request('POST', '/api/ai/analyze', json={'provider': 'openai', 'token': 'test'})
            self.assertEqual(response.status_code, 504)
            self.assertIn('45 Sekunden', response.json()['detail'])
            self.assertFalse(ai._busy.locked())
        self.run_api(check)

    def test_expired_snapshots_and_provider_error_never_echo_secrets(self):
        async def check(client):
            response = await client.post('/api/ai/analyze', json={'provider': 'openai', 'token': 'missing'})
            self.assertEqual(response.status_code, 410)
            ai._snapshots['test'] = {'created': time.monotonic(), 'payload': 'data', 'count': 1}
            database.set_setting('ai_openai_key', 'secret')
            database.set_setting('ai_openai_model', 'test-model')
            with patch.object(ai.httpx.AsyncClient, 'post', AsyncMock(return_value=httpx.Response(401, text='secret'))):
                response = await client.request('POST', '/api/ai/analyze', json={'provider': 'openai', 'token': 'test'})
            self.assertEqual(response.status_code, 502)
            self.assertNotIn('secret', response.text)
        self.run_api(check)


if __name__ == '__main__':
    unittest.main()
