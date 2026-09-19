from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from .config import Settings


class FederatoError(RuntimeError):
    """Safe, user-facing Federato integration failure."""


class FederatoClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._access_token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = asyncio.Lock()

    async def _token(self) -> str:
        if not self.settings.federato_configured:
            raise FederatoError("Federato client credentials are not configured.")
        if self._access_token and time.monotonic() < self._token_expires_at - 60:
            return self._access_token

        async with self._token_lock:
            if self._access_token and time.monotonic() < self._token_expires_at - 60:
                return self._access_token
            try:
                async with httpx.AsyncClient(
                    timeout=self.settings.request_timeout_seconds
                ) as client:
                    response = await client.post(
                        self.settings.federato_auth_url,
                        json={
                            "client_id": self.settings.federato_client_id,
                            "client_secret": self.settings.federato_client_secret,
                            "audience": self.settings.federato_audience,
                            "grant_type": "client_credentials",
                        },
                    )
                    response.raise_for_status()
                    payload = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise FederatoError("Unable to authenticate with Federato.") from exc

            token = payload.get("access_token")
            if not isinstance(token, str) or not token:
                raise FederatoError("Federato authentication returned no access token.")
            expires_in = int(payload.get("expires_in", 14_400))
            self._access_token = token
            self._token_expires_at = time.monotonic() + expires_in
            return token

    async def call(self, action: str, payload: dict[str, Any] | None = None) -> Any:
        if action not in {"schema", "query"}:
            raise ValueError(f"Unsupported Federato action: {action}")

        request_body: dict[str, Any] = {"action": action}
        if payload is not None:
            request_body["payload"] = payload

        last_error: Exception | None = None
        for attempt in range(2):
            token = await self._token()
            try:
                async with httpx.AsyncClient(
                    timeout=self.settings.request_timeout_seconds
                ) as client:
                    response = await client.post(
                        self.settings.federato_handler_url,
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Content-Type": "application/json",
                        },
                        json=request_body,
                    )
                if response.status_code == 401 and attempt == 0:
                    self._access_token = None
                    continue
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt == 0:
                        await asyncio.sleep(0.25)
                        continue
                response.raise_for_status()
                data = response.json()
                if isinstance(data, dict) and set(data) == {"output"}:
                    output = data["output"]
                    if isinstance(output, list) and output:
                        first = output[0]
                        if isinstance(first, dict) and "data" in first:
                            return first["data"]
                return data
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
                if attempt == 0:
                    await asyncio.sleep(0.25)
                    continue
            except httpx.HTTPStatusError as exc:
                message = exc.response.text[:400]
                raise FederatoError(
                    f"Federato returned HTTP {exc.response.status_code}: {message}"
                ) from exc
            except ValueError as exc:
                raise FederatoError("Federato returned invalid JSON.") from exc
        raise FederatoError("Federato request timed out after a bounded retry.") from last_error

    async def schema(self) -> Any:
        return await self.call("schema")

    async def query(self, payload: dict[str, Any]) -> Any:
        return await self.call("query", payload)
