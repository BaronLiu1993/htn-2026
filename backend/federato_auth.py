"""Auth0 client-credentials authentication for the Federato challenge API."""

from __future__ import annotations

import asyncio
import base64
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx


AUTH_TOKEN_URL = "https://auth.product.federato.ai/oauth/token"
AUDIENCE = "https://product.federato.ai/core-api"


class FederatoAuthError(RuntimeError):
    """Raised when a Federato access token cannot be obtained or read."""


@dataclass(frozen=True)
class AccessToken:
    """A JWT and the UTC time at which it becomes unusable."""

    value: str
    expires_at: datetime

    @property
    def expired(self) -> bool:
        return datetime.now(UTC) >= self.expires_at


class FederatoAuth:
    """Caches a Federato JWT and renews it when its `exp` time has passed.

    By default, reuse continues until the exact expiry instant: ``now >= exp``
    means the token is renewed. A caller may opt into a small refresh window to
    avoid starting an API call with a nearly expired token.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        http_client: httpx.AsyncClient | None = None,
        refresh_skew_seconds: int = 0,
    ) -> None:
        if not client_id or not client_secret:
            raise ValueError("Federato client_id and client_secret are required")
        if refresh_skew_seconds < 0:
            raise ValueError("refresh_skew_seconds cannot be negative")

        self._client_id = client_id
        self._client_secret = client_secret
        self._client = http_client or httpx.AsyncClient(timeout=15.0)
        self._owns_client = http_client is None
        self._refresh_skew = timedelta(seconds=refresh_skew_seconds)
        self._token: AccessToken | None = None
        self._lock = asyncio.Lock()

    @classmethod
    def from_environment(cls) -> "FederatoAuth":
        """Create an auth client from uncommitted environment variables."""
        return cls(
            client_id=os.environ.get("FEDERATO_CLIENT_ID", ""),
            client_secret=os.environ.get("FEDERATO_CLIENT_SECRET", ""),
        )

    @property
    def expires_at(self) -> datetime | None:
        return self._token.expires_at if self._token else None

    async def get_access_token(self) -> str:
        """Return a non-expired token, minting a replacement only when needed."""
        if self._usable_token():
            return self._token.value

        # A lock prevents simultaneous API calls from each minting a new token.
        async with self._lock:
            if not self._usable_token():
                self._token = await self._mint_token()
            return self._token.value

    def invalidate(self) -> None:
        """Forget the cache after a 401 so the next request mints a new token."""
        self._token = None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _usable_token(self) -> bool:
        return bool(
            self._token
            and datetime.now(UTC) < self._token.expires_at - self._refresh_skew
        )

    async def _mint_token(self) -> AccessToken:
        try:
            response = await self._client.post(
                AUTH_TOKEN_URL,
                json={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "audience": AUDIENCE,
                    "grant_type": "client_credentials",
                },
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise FederatoAuthError(f"Unable to mint Federato access token: {error}") from error

        body: dict[str, Any] = response.json()
        raw_token = body.get("access_token")
        if not isinstance(raw_token, str) or not raw_token:
            raise FederatoAuthError("Token response did not contain an access_token")

        return AccessToken(value=raw_token, expires_at=_jwt_expiry(raw_token))


def _jwt_expiry(token: str) -> datetime:
    """Read the unverified JWT `exp` claim for local cache expiry only.

    This does not authenticate the JWT; Federato's API does that. The token
    came directly from the configured Auth0 endpoint, and this decode only
    determines when this process should request another one.
    """
    try:
        payload_segment = token.split(".")[1]
        padded = payload_segment + "=" * (-len(payload_segment) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        exp = payload["exp"]
        if isinstance(exp, bool) or not isinstance(exp, (int, float)):
            raise TypeError("exp is not a Unix timestamp")
        return datetime.fromtimestamp(exp, tz=UTC)
    except (IndexError, KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FederatoAuthError("Auth0 returned a JWT without a valid exp claim") from error
