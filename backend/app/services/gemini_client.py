"""Async Gemini (Google AI Studio) client.

Drop-in replacement for the Anthropic client we previously used. Same
``call_text`` / ``call_vision`` signatures, same ``(text, usage)`` return
shape, so callers (aggregator, web_footprint, geolocation) don't change.

Gemini 2.5 charges thinking tokens against output; we fold them in via
``thoughtsTokenCount`` so the cost tracker's output total matches what
the AI Studio billing console reports.
"""

from __future__ import annotations

import base64
from typing import Any

import httpx


class GeminiError(RuntimeError):
    def __init__(self, status_code: int, body: str):
        super().__init__(f"Gemini API error {status_code}: {body[:300]}")
        self.status_code = status_code
        self.body = body


class GeminiClient:
    def __init__(
        self,
        api_key: str,
        api_url: str,
        http: httpx.AsyncClient,
    ) -> None:
        self._api_key = api_key
        # Base URL like https://generativelanguage.googleapis.com/v1beta
        self._base_url = api_url.rstrip("/")
        self._http = http

    def _endpoint(self, model: str) -> str:
        return f"{self._base_url}/models/{model}:generateContent?key={self._api_key}"

    async def _post(self, model: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self._api_key:
            raise GeminiError(401, "GEMINI_API_KEY is not configured")
        resp = await self._http.post(
            self._endpoint(model),
            json=payload,
            headers={"content-type": "application/json"},
            timeout=httpx.Timeout(60.0, connect=10.0),
        )
        if resp.status_code >= 400:
            raise GeminiError(resp.status_code, resp.text)
        return resp.json()

    @staticmethod
    def _extract_text(body: dict[str, Any]) -> str:
        candidates = body.get("candidates") or []
        if not candidates:
            return ""
        first = candidates[0] or {}
        content = first.get("content") or {}
        parts = content.get("parts") or []
        out: list[str] = []
        for part in parts:
            if isinstance(part, dict):
                t = part.get("text")
                if isinstance(t, str):
                    out.append(t)
        return "".join(out)

    @staticmethod
    def _extract_usage(body: dict[str, Any], model: str) -> dict[str, Any]:
        usage = body.get("usageMetadata", {}) or {}
        prompt = int(usage.get("promptTokenCount", 0) or 0)
        candidates = int(usage.get("candidatesTokenCount", 0) or 0)
        thoughts = int(usage.get("thoughtsTokenCount", 0) or 0)
        return {
            "input_tokens": prompt,
            "output_tokens": candidates + thoughts,
            "model": body.get("modelVersion") or model,
        }

    async def call_text(
        self,
        model: str,
        system: str,
        user_content: str,
        max_tokens: int = 4096,
        response_json: bool = False,
    ) -> tuple[str, dict]:
        gen_config: dict[str, Any] = {"maxOutputTokens": int(max_tokens)}
        if response_json:
            gen_config["responseMimeType"] = "application/json"
        payload: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": user_content}]}],
            "generationConfig": gen_config,
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        body = await self._post(model, payload)
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
        parts: list[dict[str, Any]] = [
            {
                "inline_data": {
                    "mime_type": image_media_type,
                    "data": b64,
                }
            },
            {"text": prompt},
        ]
        payload: dict[str, Any] = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"maxOutputTokens": int(max_tokens)},
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        body = await self._post(model, payload)
        return self._extract_text(body), self._extract_usage(body, model)
