"""
Async REST client for OUR self-hosted tldraw backend, not tldraw.com.

The React SDK + Node sync service live under whiteboard/.
"""
from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import urlsplit

import httpx


BOARD_ID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)


class TldrawClient:
    def __init__(self) -> None:
        base_url = os.getenv("TLDRAW_API_URL", "http://127.0.0.1:5858").rstrip("/")
        api_key = os.getenv("TLDRAW_SERVICE_API_KEY", "").strip()
        if not api_key:
            raise ValueError("TLDRAW_SERVICE_API_KEY is not configured")

        self.public_url = os.getenv("TLDRAW_PUBLIC_URL", "https://discord-whiteboard.fly.dev").rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"X-API-Key": api_key, "Accept": "application/json"},
            timeout=httpx.Timeout(30.0, connect=10.0),
        )

    async def __aenter__(self) -> TldrawClient:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        await self._client.aclose()

    @staticmethod
    def parse_board_id(value: str) -> str:
        value = value.strip().strip("<>")
        if value.startswith(("https://", "http://")):
            parsed = urlsplit(value)
            match = re.fullmatch(r"/b/([^/]+)/?", parsed.path)
            if not match or not parsed.hostname:
                raise ValueError("Use a tldraw board link (/b/<id>) or its UUID")
            value = match.group(1)
        if not BOARD_ID_PATTERN.fullmatch(value):
            raise ValueError("Invalid board ID. Paste the /tldraw board URL or UUID")
        return value.lower()

    def board_url(self, board: str) -> str:
        return f"{self.public_url}/b/{self.parse_board_id(board)}"

    async def create_board(self, title: str) -> dict[str, Any]:
        title = title.strip()
        if not 1 <= len(title) <= 60:
            raise ValueError("Board title must contain 1 to 60 characters")
        response = await self._client.post("/api/boards", json={"title": title})
        response.raise_for_status()
        return response.json()

    async def get_sticky_notes(self, board: str) -> list[dict[str, Any]]:
        board_id = self.parse_board_id(board)
        response = await self._client.get(f"/api/boards/{board_id}/notes")
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or not isinstance(data.get("notes"), list):
            raise ValueError("Unexpected tldraw backend response")
        return data["notes"]
