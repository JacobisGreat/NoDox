from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.config import Settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class OAuthServiceError(Exception):
    pass


class MetaOAuthService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def build_authorize_url(self, state: str) -> str:
        params = {
            "client_id": self.settings.meta_client_id,
            "redirect_uri": str(self.settings.meta_redirect_uri),
            "scope": self.settings.meta_scope,
            "response_type": "code",
            "state": state,
        }
        return f"{self.settings.meta_auth_url}?{urlencode(params)}"

    async def exchange_code(self, code: str) -> dict[str, Any]:
        if not self.settings.meta_client_id or not self.settings.meta_client_secret:
            raise OAuthServiceError(
                "Meta OAuth credentials are not configured on the backend."
            )

        payload = {
            "client_id": self.settings.meta_client_id,
            "client_secret": self.settings.meta_client_secret,
            "grant_type": "authorization_code",
            "redirect_uri": str(self.settings.meta_redirect_uri),
            "code": code,
        }

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                str(self.settings.meta_token_url),
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

        if response.status_code >= 400:
            raise OAuthServiceError(
                f"Token exchange failed ({response.status_code}): {response.text}"
            )

        token_data: dict[str, Any] = response.json()
        access_token = token_data.get("access_token")
        if not access_token:
            raise OAuthServiceError("Token exchange succeeded but no access token was returned.")

        if self.settings.meta_exchange_long_lived_token:
            token_data = await self._exchange_for_long_lived(access_token)

        return token_data

    async def _exchange_for_long_lived(self, short_lived_token: str) -> dict[str, Any]:
        params = {
            "grant_type": "ig_exchange_token",
            "client_secret": self.settings.meta_client_secret,
            "access_token": short_lived_token,
        }
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(str(self.settings.meta_long_lived_token_url), params=params)

        if response.status_code >= 400:
            raise OAuthServiceError(
                f"Long-lived token exchange failed ({response.status_code}): {response.text}"
            )
        return response.json()

    async def fetch_me(self, access_token: str) -> dict[str, Any]:
        fields = "id,username,account_type,media_count"
        url = f"{self.settings.meta_graph_base_url}/me"
        params = {"fields": fields, "access_token": access_token}
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(url, params=params)

        if response.status_code >= 400:
            raise OAuthServiceError(
                f"GET /me failed ({response.status_code}): {response.text}"
            )
        return response.json()

    async def fetch_me_media(self, access_token: str, limit: int = 25) -> list[dict[str, Any]]:
        fields = "id,caption,media_type,media_url,thumbnail_url,permalink,timestamp"
        url = f"{self.settings.meta_graph_base_url}/me/media"
        params = {"fields": fields, "limit": limit, "access_token": access_token}
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(url, params=params)

        if response.status_code >= 400:
            raise OAuthServiceError(
                f"GET /me/media failed ({response.status_code}): {response.text}"
            )

        payload = response.json()
        data = payload.get("data", [])
        if not isinstance(data, list):
            raise OAuthServiceError("GET /me/media returned an unexpected response shape.")
        return data

    @staticmethod
    def extract_expiry(token_data: dict[str, Any]) -> datetime | None:
        expires_in = token_data.get("expires_in")
        if not expires_in:
            return None
        try:
            return utcnow() + timedelta(seconds=int(expires_in))
        except (TypeError, ValueError):
            return None

