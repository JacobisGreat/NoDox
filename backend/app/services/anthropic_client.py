"""Thin async wrapper around the Anthropic Messages HTTP API.

We deliberately use raw httpx rather than the Anthropic SDK so we can
keep the dependency surface small and control retries/timeouts ourselves.
"""

from __future__ import annotations

import base64
from typing import Any

import httpx


class AnthropicError(RuntimeError):
    def __init__(self, status_code: int, body: str):
        super().__init__(f"Anthropic API error {status_code}: {body[:300]}")
        self.status_code = status_code
        self.body = body


class AnthropicClient:
    def __init__(
        self,
        api_key: str,
        api_url: str,
        version: str,
        http: httpx.AsyncClient,
    ) -> None:
        self._api_key = api_key
        self._api_url = api_url
        self._version = version
        self._http = http

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._api_key,
            "anthropic-version": self._version,
            "content-type": "application/json",
        }

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self._api_key:
            raise AnthropicError(401, "ANTHROPIC_API_KEY is not configured")
        resp = await self._http.post(
            self._api_url,
            json=payload,
            headers=self._headers,
            timeout=httpx.Timeout(60.0, connect=10.0),
        )
        if resp.status_code >= 400:
            raise AnthropicError(resp.status_code, resp.text)
        return resp.json()

    @staticmethod
    def _extract_text(body: dict[str, Any]) -> str:
        parts = []
        for block in body.get("content", []) or []:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts)

    @staticmethod
    def _extract_usage(body: dict[str, Any], model: str) -> dict[str, Any]:
        usage = body.get("usage", {}) or {}
        return {
            "input_tokens": int(usage.get("input_tokens", 0) or 0),
            "output_tokens": int(usage.get("output_tokens", 0) or 0),
            "model": body.get("model") or model,
        }

    async def call_text(
        self,
        model: str,
        system: str,
        user_content: str,
        max_tokens: int = 4096,
        response_json: bool = False,
    ) -> tuple[str, dict]:
        """Plain text → text completion. Returns (text, usage)."""
        sys_prompt = system
        if response_json:
            sys_prompt = (
                (system.rstrip() + "\n\n" if system else "")
                + "Respond with only valid JSON, no markdown fences."
            )
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": sys_prompt,
            "messages": [{"role": "user", "content": user_content}],
        }
        body = await self._post(payload)
        return self._extract_text(body), self._extract_usage(body, model)

    async def call_vision(
        self,
        model: str,
        system: str,
        image_bytes: bytes,
        image_media_type: str,
        prompt: str,
        max_tokens: int = 2048,
    ) -> tuple[str, dict]:
        """Single image + prompt. Returns (text, usage)."""
        b64 = base64.standard_b64encode(image_bytes).decode("ascii")
        content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": image_media_type,
                    "data": b64,
                },
            },
            {"type": "text", "text": prompt},
        ]
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": content}],
        }
        body = await self._post(payload)
        return self._extract_text(body), self._extract_usage(body, model)
