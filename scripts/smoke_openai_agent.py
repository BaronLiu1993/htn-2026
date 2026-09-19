from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.config import Settings
from backend.app.models import BatchAnalysisRequest
from backend.app.service import UnderwriteService


async def main() -> int:
    if not os.getenv("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not configured; no API request was made.")
        return 2

    settings = Settings.from_env()
    service = UnderwriteService(settings)
    run = await service.analyze(BatchAnalysisRequest(submission_ids=["101"]))
    query_events = [
        event for event in run.trace if event.tool == "agent_query_federato"
    ]
    print(f"run_status={run.status}")
    print(f"agent_mode={run.agent_mode}")
    print(f"agent_model={run.agent_model or 'none'}")
    print(f"dynamic_queries={len(query_events)}")
    print(
        "openai_explanations="
        + str(sum(item.explanation_source == "openai" for item in run.assessments))
    )
    if run.agent_mode != "openai":
        failures = [event.error for event in run.trace if event.status == "failure" and event.error]
        print(f"failure={failures[-1] if failures else 'unknown'}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
