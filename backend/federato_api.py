"""Client for the Federato Hack the North integration API."""

from __future__ import annotations

from typing import Any

import httpx

from federato_auth import FederatoAuth, FederatoAuthError


FEDERATO_API_URL = (
    "https://product.federato.ai/integrations-api/handlers/"
    "federato-hack-north?outputOnly=true"
)


class FederatoAPIError(RuntimeError):
    """Raised when the Federato API returns an unsuccessful response."""


class FederatoClient:
    """Async client for schema discovery and subsequent data queries."""

    def __init__(
        self,
        auth: FederatoAuth,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._auth = auth
        self._client = http_client or httpx.AsyncClient(timeout=30.0)
        self._owns_client = http_client is None

    async def fetch_schema(self) -> dict[str, Any]:
        """Fetch and return the API's current resource/field schema."""
        body = await self._post_action({"action": "schema"})
        if not isinstance(body, dict):
            raise FederatoAPIError("Schema response was not a JSON object")
        return body

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _post_action(self, payload: dict[str, Any]) -> Any:
        # Retry once after a 401: the token may have been revoked before its
        # exp claim, so invalidate the cache and mint a fresh one.
        for attempt in range(2):
            try:
                token = await self._auth.get_access_token()
            except FederatoAuthError:
                raise
            response = await self._client.post(
                FEDERATO_API_URL,
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )
            if response.status_code == 401 and attempt == 0:
                self._auth.invalidate()
                continue
            try:
                response.raise_for_status()
            except httpx.HTTPError as error:
                detail = response.text[:500]
                raise FederatoAPIError(
                    f"Federato {payload.get('action', 'request')} failed "
                    f"with HTTP {response.status_code}: {detail}"
                ) from error
            try:
                return response.json()
            except ValueError as error:
                raise FederatoAPIError("Federato API returned invalid JSON") from error

        raise FederatoAPIError("Federato API request was unauthorized after token refresh")
