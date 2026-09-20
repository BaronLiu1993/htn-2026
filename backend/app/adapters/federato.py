from __future__ import annotations

from typing import Any

from ..demo_federato import DEMO_SCHEMA, query_demo
from ..federato_client import FederatoClient


class FederatoAdapter:
    name = "federato"
    tool_class = "federato"

    def __init__(self, client: FederatoClient) -> None:
        self.client = client

    async def execute(self, action: str, payload: dict[str, Any] | None = None) -> Any:
        if action == "schema":
            return await self.client.schema()
        if action == "query" and payload is not None:
            return await self.client.query(payload)
        raise ValueError(f'Unsupported Federato adapter action "{action}".')


class DemoFederatoAdapter:
    name = "federato"
    tool_class = "federato"

    async def execute(self, action: str, payload: dict[str, Any] | None = None) -> Any:
        if action == "schema":
            return DEMO_SCHEMA
        if action == "query" and payload is not None:
            return query_demo(payload)
        raise ValueError(f'Unsupported demo adapter action "{action}".')
