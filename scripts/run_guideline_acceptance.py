from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.config import Settings
from backend.app.models import BatchAnalysisRequest
from backend.app.rule_engine import matches
from backend.app.schema_registry import SchemaRegistry
from backend.app.service import UnderwriteService


def _rows(payload: Any) -> tuple[list[dict[str, Any]], int | None]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)], len(payload)
    if not isinstance(payload, dict):
        return [], None
    total = payload.get("total") if isinstance(payload.get("total"), int) else None
    for key in ("records", "results", "items", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)], total
        if isinstance(value, dict):
            rows, nested_total = _rows(value)
            if rows:
                return rows, total if total is not None else nested_total
    return [], total


def _value(record: dict[str, Any], path: str) -> Any:
    current: Any = record
    for segment in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(segment)
    return current


async def _independent_scope(
    service: UnderwriteService,
    guideline_id: str = "guideline-a",
) -> dict[str, Any]:
    package = service.guidelines.resolve(guideline_id)
    raw_schema = await service.client.schema()
    registry = SchemaRegistry(raw_schema)
    resource = registry.find_resource(package.scope.source.resource)
    if resource is None:
        raise RuntimeError("The independent scope check could not find Submission.")
    identifier_field = registry.identifier_field(resource)
    scope_field = package.scope.source.field
    query_base = {
        "resource": resource,
        "select": [identifier_field, scope_field],
    }
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    duplicates: list[str] = []
    offsets: list[int] = []
    total: int | None = None
    offset = 0
    limit = 100
    while True:
        query = {
            **query_base,
            "pagination": {"limit": limit, "offset": offset},
        }
        registry.validate_query(query)
        page_payload = await service.client.query(query)
        page, reported_total = _rows(page_payload)
        offsets.append(offset)
        if reported_total is not None:
            total = reported_total
        for row in page:
            identifier = row.get(identifier_field)
            if identifier is None:
                continue
            rendered = str(identifier)
            if rendered in seen:
                duplicates.append(rendered)
            seen.add(rendered)
            rows.append(row)
        if len(page) < limit or (total is not None and len(rows) >= total):
            break
        if not page:
            raise RuntimeError("Independent scope pagination stopped making progress.")
        offset += limit

    in_scope: list[str] = []
    outside_scope: list[str] = []
    unknown: list[str] = []
    for row in rows:
        identifier = row.get(identifier_field)
        if identifier is None:
            continue
        rendered = str(identifier)
        value = _value(row, scope_field)
        if value is None or value == "":
            unknown.append(rendered)
        elif matches(package.scope.operator, value, package.scope.value):
            in_scope.append(rendered)
        else:
            outside_scope.append(rendered)
    return {
        "schema_digest": registry.schema_digest(),
        "identifier_field": identifier_field,
        "scope_field": scope_field,
        "reported_total": total,
        "available_ids": sorted(seen),
        "in_scope_ids": sorted(set(in_scope)),
        "outside_scope_ids": sorted(set(outside_scope)),
        "scope_unknown_ids": sorted(set(unknown)),
        "duplicate_ids": sorted(set(duplicates)),
        "page_offsets": offsets,
        "missed_page": total is not None and len(seen) != total,
    }


async def main(guideline_id: str = "guideline-a") -> int:
    settings = Settings.from_env()
    if not settings.federato_configured or not settings.openai_configured:
        print("Live acceptance requires configured Federato and OpenAI credentials.")
        return 2

    service = UnderwriteService(settings)
    package = service.guidelines.resolve(guideline_id)
    expected = await _independent_scope(service, guideline_id)
    run = await service.analyze(
        BatchAnalysisRequest(
            guideline_id=package.id,
            guideline_version=package.version,
            force_schema_refresh=True,
        )
    )
    status_counts = Counter(item.status for item in run.assessments)
    adapter_events = [event for event in run.trace if event.adapter]
    failed_events = [event for event in run.trace if event.status == "failure"]
    assessed_ids = [item.submission_id for item in run.assessments]
    assessed_set = set(assessed_ids)
    expected_set = set(expected["in_scope_ids"])
    run_artifact = service.get_run_artifact(run.run_id) or {}
    schema_digest_matches = (
        run_artifact.get("schema_digest") == expected["schema_digest"]
    )
    duplicate_assessments = sorted(
        identifier
        for identifier, count in Counter(assessed_ids).items()
        if count > 1
    )
    missed_assessments = sorted(expected_set - assessed_set)
    unrelated_assessments = sorted(assessed_set - expected_set)
    report = {
        "run_id": run.run_id,
        "status": run.status,
        "mode": run.mode,
        "guideline": f"{run.guideline_id}@{run.guideline_version}",
        "schema_source": run.schema_source,
        "available_submissions": run.available_submissions,
        "in_scope_submissions": run.in_scope_submissions,
        "outside_scope_submissions": run.outside_scope_submissions,
        "scope_unknown_submissions": run.scope_unknown_submissions,
        "assessment_count": len(run.assessments),
        "status_counts": dict(sorted(status_counts.items())),
        "unresolved_fact_count": run.unresolved_fact_count,
        "duration_ms": run.duration_ms,
        "query_count": run.query_count,
        "useful_fact_changes": run.useful_fact_changes,
        "query_metrics": run.query_metrics,
        "unresolved_facts_by_reason": run.unresolved_facts_by_reason,
        "agent_stop_reason": run.agent_stop_reason,
        "openai_model": run.agent_model,
        "tool_call_count": run.tool_call_count,
        "traced_adapter_actions": len(adapter_events),
        "failed_trace_events": len(failed_events),
        "duplicate_scope_ids": expected["duplicate_ids"],
        "scope_pagination_offsets": expected["page_offsets"],
        "scope_missed_page": expected["missed_page"],
        "schema_digest_matches": schema_digest_matches,
        "duplicate_assessments": duplicate_assessments,
        "missed_assessments": missed_assessments,
        "unrelated_assessments": unrelated_assessments,
        "errors": run.errors,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    artifact = {
        "created_at": datetime.now().isoformat(),
        "acceptance": report,
        "independent_scope": expected,
        "run": run.model_dump(mode="json"),
        **run_artifact,
    }
    output_directory = (
        Path(__file__).resolve().parents[1]
        / "artifacts"
        / "underwriting-runs"
    )
    output_directory.mkdir(parents=True, exist_ok=True)
    artifact_path = output_directory / f"{run.run_id}.json"
    artifact_path.write_text(
        json.dumps(artifact, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(f"Acceptance artifact: {artifact_path}")
    accepted = (
        run.status == "completed"
        and run.mode == "live"
        and run.schema_source == "live"
        and run.available_submissions == len(expected["available_ids"])
        and run.in_scope_submissions == len(expected["in_scope_ids"])
        and run.outside_scope_submissions == len(expected["outside_scope_ids"])
        and run.scope_unknown_submissions == len(expected["scope_unknown_ids"])
        and assessed_set == expected_set
        and len(assessed_ids) == len(assessed_set)
        and not expected["duplicate_ids"]
        and not expected["missed_page"]
        and schema_digest_matches
        and not duplicate_assessments
        and not missed_assessments
        and not unrelated_assessments
        and not failed_events
        and all(item.guideline_version == package.version for item in run.assessments)
        and all(item.ledger is not None for item in run.assessments)
    )
    return 0 if accepted else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--guideline", default="guideline-a")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.guideline)))
