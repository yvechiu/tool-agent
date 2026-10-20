import json
import os
import unittest
from unittest.mock import patch

import httpx
from langchain_core.messages import HumanMessage, SystemMessage
from utils.gemini_utils import GeminiLLM


class GeminiTests(unittest.IsolatedAsyncioTestCase):
    async def call(self, messages, handler):
        # Real httpx request serialization, intercepted before network access.
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch.dict(os.environ, {'GEMINI_API_KEY':'unit-test-placeholder', 'GEMINI_MODEL':'gemini-2.5-flash'}), patch('utils.gemini_utils.httpx.AsyncClient', return_value=client):
            return await GeminiLLM().ainvoke(messages)

    async def test_summary_uses_google_and_json_output(self):
        def handler(req):
            self.assertEqual(req.url.host, 'generativelanguage.googleapis.com')
            self.assertNotIn('key=', str(req.url))
            self.assertEqual(req.headers['x-goog-api-key'], 'unit-test-placeholder')
            body = json.loads(req.content)
            self.assertEqual(body['generationConfig']['responseMimeType'], 'application/json')
            self.assertEqual(body['system_instruction']['parts'][0]['text'], '摘要指示')
            return httpx.Response(200, json={'candidates':[{'content':{'parts':[{'text':'{"Title":"摘要"}'}]}}]})
        result = await self.call([SystemMessage(content='摘要指示')], handler)
        self.assertEqual(json.loads(result.content)['Title'], '摘要')

    async def test_whiteboard_separates_system_and_notes(self):
        def handler(req):
            body = json.loads(req.content)
            self.assertEqual(body['contents'][0]['parts'][0]['text'], '便利貼')
            self.assertNotIn('responseMimeType', body['generationConfig'])
            return httpx.Response(200, json={'candidates':[{'content':{'parts':[{'text':'秘密推理','thought':True}, {'text':'分析'}, {'text':'結果'}]}}]})
        result = await self.call([SystemMessage(content='分析指示'), HumanMessage(content='便利貼')], handler)
        self.assertEqual(result.content, '分析結果')

    async def test_error_does_not_disclose_provider_body(self):
        with self.assertRaisesRegex(RuntimeError, 'HTTP 429') as error:
            await self.call([SystemMessage(content='摘要')], lambda _: httpx.Response(429, json={'error':'private detail'}))
        self.assertNotIn('private detail', str(error.exception))

    async def test_blocked_response_is_not_empty_success(self):
        with self.assertRaisesRegex(RuntimeError, '未產生文字'):
            await self.call([SystemMessage(content='摘要')], lambda _: httpx.Response(200, json={'promptFeedback':{'blockReason':'SAFETY'}}))

    async def test_missing_key_fails_before_network(self):
        with patch.dict(os.environ, {'GEMINI_API_KEY':''}):
            with self.assertRaisesRegex(ValueError, 'GEMINI_API_KEY'):
                await GeminiLLM().ainvoke([SystemMessage(content='摘要')])
