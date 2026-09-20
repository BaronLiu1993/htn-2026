from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.config import Settings
from backend.app.models import BatchAnalysisRequest
from backend.app.service import UnderwriteService


async def main() -> int:
    settings = Settings.from_env()
    if not settings.federato_configured or not settings.openai_configured:
        print("Live acceptance requires configured Federato and OpenAI credentials.")
        return 2

    service = UnderwriteService(settings)
    run = await service.analyze(
        BatchAnalysisRequest(
            guideline_id="guideline-a",
            guideline_version="2025.1",
            force_schema_refresh=True,
        )
    )
    status_counts = Counter(item.status for item in run.assessments)
    adapter_events = [event for event in run.trace if event.adapter]
    failed_events = [event for event in run.trace if event.status == "failure"]
    report = {
        "run_id": run.run_id,
        "status": run.status,
        "mode": run.mode,
        "guideline": f"{run.guideline_id}@{run.guideline_version}",
        "schema_source": run.schema_source,
        "total_submissions": run.total_submissions,
        "applicable_submissions": run.applicable_submissions,
        "not_applicable_submissions": run.not_applicable_submissions,
        "assessment_count": len(run.assessments),
        "status_counts": dict(sorted(status_counts.items())),
        "unresolved_fact_count": run.unresolved_fact_count,
        "duration_ms": run.duration_ms,
        "query_count": run.query_count,
        "useful_fact_changes": run.useful_fact_changes,
        "openai_model": run.agent_model,
        "tool_call_count": run.tool_call_count,
        "traced_adapter_actions": len(adapter_events),
        "failed_trace_events": len(failed_events),
        "errors": run.errors,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    accepted = (
        run.status == "completed"
        and run.mode == "live"
        and run.schema_source == "live"
        and run.total_submissions == 158
        and run.applicable_submissions == 158
        and run.not_applicable_submissions == 0
        and len(run.assessments) == 158
        and not failed_events
        and all(item.guideline_version == "2025.1" for item in run.assessments)
        and all(item.ledger is not None for item in run.assessments)
    )
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
