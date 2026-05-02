"""Async Anthropic Messages API client.

Drop-in replacement for the Gemini client. Same ``call_text`` /
``call_vision`` signatures and ``(text, usage)`` return shape so the
aggregator and pipelines don't change. Usage dict matches what
``cost_tracker.record_anthropic`` already expects.
"""

from __future__ import annotations

import base64
from typing import Any

import httpx


_ANTHROPIC_VERSION = "2023-06-01"
_ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"


class AnthropicError(RuntimeError):
    def __init__(self, status_code: int, body: str):
        super().__init__(f"Anthropic API error {status_code}: {body[:300]}")
        self.status_code = status_code
        self.body = body


class AnthropicClient:
    def __init__(self, api_key: str, http: httpx.AsyncClient) -> None:
        self._api_key = api_key
        self._http = http

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self._api_key:
            raise AnthropicError(401, "ANTHROPIC_API_KEY is not configured")
        resp = await self._http.post(
            _ANTHROPIC_MESSAGES_URL,
            json=payload,
            headers=self._headers(),
            timeout=httpx.Timeout(120.0, connect=10.0),
        )
        if resp.status_code >= 400:
            raise AnthropicError(resp.status_code, resp.text)
        return resp.json()

    @staticmethod
    def _extract_text(body: dict[str, Any]) -> str:
        blocks = body.get("content") or []
        out: list[str] = []
        for b in blocks:
            if isinstance(b, dict) and b.get("type") == "text":
                t = b.get("text")
                if isinstance(t, str):
                    out.append(t)
        return "".join(out)

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
        # Anthropic doesn't have a hard JSON mode like Gemini, so we
        # nudge the model with a system suffix when the caller wants
        # JSON. _parse_json_response in the pipelines already strips
        # code fences if the model adds them.
        sys_text = system or ""
        if response_json:
            sys_text = (
                (sys_text + "\n\n").strip()
                + "\n\nReturn ONLY a single valid JSON object. "
                "No prose, no preamble, no code fences."
            )
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": int(max_tokens),
            "messages": [{"role": "user", "content": user_content}],
        }
        if sys_text:
            payload["system"] = sys_text
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
        b64 = base64.standard_b64encode(image_bytes).decode("ascii")
        content: list[dict[str, Any]] = [
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
            "max_tokens": int(max_tokens),
            "messages": [{"role": "user", "content": content}],
        }
        if system:
            payload["system"] = system
        body = await self._post(payload)
        return self._extract_text(body), self._extract_usage(body, model)
