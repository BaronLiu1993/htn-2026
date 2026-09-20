"""Optional state context. FEMA declarations never change appetite status."""
from __future__ import annotations

import asyncio
import re
import time
from datetime import date, datetime
from uuid import uuid4

import httpx

from .models import Assessment, TraceEvent

FEMA_URL = "https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries"


async def enrich_disasters(assessments: list[Assessment], trace: list[TraceEvent]) -> None:
    states = sorted({a.primary_state for a in assessments if a.primary_state and re.fullmatch(r"[A-Z]{2}", a.primary_state)})
    if not states:
        return
    today = date.today()
    since = today.replace(year=today.year - 5, day=min(today.day, 28)).isoformat()
    started = time.perf_counter()
    event = TraceEvent(id=f"trace_{uuid4().hex[:10]}", tool="external_enrichment",
                       purpose="Use recent state disaster declarations to break otherwise equal review priorities.",
                       status="success", started_at=datetime.now(), duration_ms=1, result_summary="")

    async def fetch() -> dict[str, set[int]]:
        declarations: dict[str, set[int]] = {state: set() for state in states}
        state_filter = " or ".join(f"state eq '{state}'" for state in states)
        async with httpx.AsyncClient(timeout=5) as client:
            for offset in range(0, 100000, 10000):
                response = await client.get(FEMA_URL, params={
                    "$filter": f"({state_filter}) and declarationDate ge '{since}T00:00:00.000Z'",
                    "$select": "state,disasterNumber", "$orderby": "id",
                    "$top": 10000, "$skip": offset,
                })
                response.raise_for_status()
                rows = response.json()["DisasterDeclarationsSummaries"]
                if not isinstance(rows, list):
                    raise ValueError("Invalid FEMA response")
                for row in rows:
                    declarations[row["state"]].add(int(row["disasterNumber"]))
                if len(rows) < 10000:
                    return declarations
        raise ValueError("FEMA pagination incomplete")

    try:
        declarations = await asyncio.wait_for(fetch(), timeout=10)
        for assessment in assessments:
            if assessment.primary_state in declarations:
                assessment.disaster_declaration_count = len(declarations[assessment.primary_state])
                assessment.disaster_context_since = since
                assessment.disaster_context_retrieved_at = datetime.now()
        event.result_summary = f"Looked up FEMA declarations for {len(states)} states. Fewer distinct declarations break ties after target fit and evidence completeness."
    except (httpx.HTTPError, ValueError, KeyError, TypeError, TimeoutError):
        event.status = "retry"
        event.result_summary = "FEMA context is unavailable. The queue uses guideline ranking without the optional tie-breaker."
    event.duration_ms = max(1, int((time.perf_counter() - started) * 1000))
    trace.append(event)
