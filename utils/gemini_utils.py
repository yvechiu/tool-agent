"""Gemini REST adapter for the bot's existing async message interface."""
import os
import re
from types import SimpleNamespace

import httpx
from dotenv import load_dotenv

load_dotenv('.env')


class GeminiLLM:
    async def ainvoke(self, messages):
        api_key = os.getenv('GEMINI_API_KEY', '').strip()
        if not api_key:
            raise ValueError('請在 .env 設定 GEMINI_API_KEY。')
        model = os.getenv('GEMINI_MODEL', 'gemini-2.5-flash').strip()
        if not re.fullmatch(r'[A-Za-z0-9._-]+', model):
            raise ValueError('GEMINI_MODEL 必須是模型名稱，例如 gemini-2.5-flash。')
        system, contents = [], []
        for message in messages:
            if message.type == 'system':
                system.append({'text': message.content})
            else:
                contents.append({
                    'role': 'model' if message.type == 'ai' else 'user',
                    'parts': [{'text': message.content}],
                })
        # Intent and summary currently supply their full prompt as a system message.
        config = {'temperature': 0}
        if not contents:
            contents = [{'role': 'user', 'parts': [{'text': '依照指示輸出 JSON。'}]}]
            config['responseMimeType'] = 'application/json'
        payload = {'contents': contents, 'generationConfig': config}
        if system:
            payload['system_instruction'] = {'parts': system}
        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.post(
                f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',
                headers={'x-goog-api-key': api_key}, json=payload,
            )
            # Do not expose request headers, keys, or provider response bodies.
            if response.is_error:
                raise RuntimeError(f'Gemini API 回傳 HTTP {response.status_code}，請檢查金鑰、模型與配額。')
            data = response.json()
        candidates = data.get('candidates', [])
        parts = candidates[0].get('content', {}).get('parts', []) if candidates else []
        text = ''.join(part.get('text', '') for part in parts if not part.get('thought'))
        if not text.strip():
            raise RuntimeError('Gemini 未產生文字，請檢查內容限制或稍後再試。')
        return SimpleNamespace(content=text)


llm = GeminiLLM()
