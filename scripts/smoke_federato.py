from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.config import Settings
from backend.app.federato_client import FederatoClient
from backend.app.schema_registry import SchemaRegistry


async def main() -> int:
    settings = Settings.from_env()
    if not settings.federato_configured:
        print("Federato credentials are not configured; no request was made.")
        return 2

    client = FederatoClient(settings)
    schema = await client.schema()
    registry = SchemaRegistry(schema)
    submission = registry.find_resource("Submission")
    print(f"schema_resources={len(registry.resources)}")
    print(f"submission_resource={submission or 'not-found'}")
    if not submission:
        return 1

    result = await client.query(
        {"resource": submission, "pagination": {"limit": 1, "offset": 0}}
    )
    total = result.get("total") if isinstance(result, dict) else None
    print(f"query_total={total if total is not None else 'unavailable'}")
    print("federato_smoke=passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
