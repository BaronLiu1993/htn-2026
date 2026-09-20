from __future__ import annotations

from .federato_client import repairable_query_error

import asyncio
import json
import re
import time
from datetime import datetime
from typing import Any, Protocol
from uuid import uuid4

from .guideline_registry import ToolPolicy
from .models import QueryAudit, TraceEvent
from .schema_registry import QueryValidationError, SchemaRegistry


class ToolAdapter(Protocol):
    name: str
    tool_class: str

    async def execute(self, action: str, payload: dict[str, Any] | None = None) -> Any: ...


class ToolGateway:
    """Validate, budget, execute, and trace every external data lookup."""

    def __init__(
        self,
        adapters: list[ToolAdapter],
        policy: ToolPolicy,
        trace: list[TraceEvent],
    ) -> None:
        self.adapters = {adapter.name: adapter for adapter in adapters}
        self.policy = policy
        self.trace = trace
        self.registry: SchemaRegistry | None = None
        self.calls = 0
        self.query_audits: list[QueryAudit] = []
        missing = set(policy.required_adapters) - set(self.adapters)
        if missing:
            raise RuntimeError(f"Required adapters are not configured: {', '.join(sorted(missing))}.")
        forbidden = {
            adapter.name
            for adapter in adapters
            if adapter.tool_class not in policy.allowed_tool_classes
        }
        if forbidden:
            raise RuntimeError(f"Adapters are not allowed by the package: {', '.join(sorted(forbidden))}.")

    @property
    def budget_remaining(self) -> int:
        return max(0, self.policy.max_calls - self.calls)

    async def schema(self, *, purpose: str = "Find the available sources of underwriting evidence.") -> Any:
        result = await self._execute("federato", "schema", None, purpose=purpose)
        self.registry = SchemaRegistry(result)
        return result

    async def query(
        self,
        payload: dict[str, Any],
        *,
        purpose: str = "Need source evidence.",
        fact_ids: list[str] | None = None,
        submission_id: str | None = None,
    ) -> Any:
        if self.registry is None:
            raise RuntimeError("Schema discovery must run before a source query.")
        started_at = datetime.now()
        started = time.perf_counter()
        audit_id = f"query_{uuid4().hex[:10]}"
        payload = self.registry.coerce_query(payload)
        safe_payload = json.loads(json.dumps(payload, default=str))
        try:
            self.registry.validate_query(payload)
            result = await self._execute(
                "federato",
                "query",
                payload,
                purpose=purpose,
                fact_ids=fact_ids or [],
                submission_id=submission_id,
            )
            if self.trace and self.trace[-1].tool == "federato_query":
                self.trace[-1].source_resource = str(payload.get("resource") or "")
                self.trace[-1].fact_ids = fact_ids or self.trace[-1].fact_ids
        except Exception as exc:
            if (repairable_query_error(exc) and self.trace
                    and self.trace[-1].tool == "federato_query"
                    and self.trace[-1].status == "failure"):
                self.trace[-1].status = "retry"
                self.trace[-1].result_summary = "The query needs a different field path. The search will be revised."
            self.query_audits.append(
                QueryAudit(
                    id=audit_id,
                    payload=safe_payload,
                    schema_digest=self.registry.schema_digest(),
                    resource=str(payload.get("resource") or ""),
                    pagination=_pagination(payload),
                    started_at=started_at,
                    duration_ms=max(1, int((time.perf_counter() - started) * 1000)),
                    status="failure",
                    failed_field_path=_failed_field_path(exc),
                    error=str(exc),
                )
            )
            raise
        returned_count, returned_total = _result_counts(result)
        if self.trace and self.trace[-1].tool == "federato_query":
            self.trace[-1].records_inspected = returned_count
            self.trace[-1].source_resource = str(payload.get("resource") or self.trace[-1].source_resource or "")
            if not self.trace[-1].facts_changed:
                self.trace[-1].facts_changed = 0
        self.query_audits.append(
            QueryAudit(
                id=audit_id,
                payload=safe_payload,
                schema_digest=self.registry.schema_digest(),
                resource=str(payload.get("resource") or ""),
                pagination=_pagination(payload),
                started_at=started_at,
                duration_ms=max(1, int((time.perf_counter() - started) * 1000)),
                returned_count=returned_count,
                returned_total=returned_total,
                status="success",
            )
        )
        return result

    def annotate_query(self, audit_id: str | None, diagnostics: dict[str, Any]) -> None:
        if not self.query_audits:
            return
        audit = (
            next((item for item in reversed(self.query_audits) if item.id == audit_id), None)
            if audit_id
            else self.query_audits[-1]
        )
        if audit is not None:
            audit.attribution.update(diagnostics)

    @property
    def last_query_audit_id(self) -> str | None:
        return self.query_audits[-1].id if self.query_audits else None

    async def _execute(
        self,
        adapter_name: str,
        action: str,
        payload: dict[str, Any] | None,
        *,
        purpose: str,
        fact_ids: list[str] | None = None,
        submission_id: str | None = None,
    ) -> Any:
        if self.calls >= self.policy.max_calls:
            raise RuntimeError("The package tool-call budget has been exhausted.")
        adapter = self.adapters.get(adapter_name)
        if adapter is None:
            raise RuntimeError(f'Required adapter "{adapter_name}" is unavailable.')
        self.calls += 1
        started = time.perf_counter()
        timestamp = datetime.now()
        tool_name = f"{adapter_name}_{action}"
        try:
            result = await asyncio.wait_for(
                adapter.execute(action, payload), timeout=self.policy.timeout_seconds
            )
        except Exception as exc:
            self.trace.append(
                TraceEvent(
                    id=f"trace_{uuid4().hex[:10]}",
                    submission_id=submission_id,
                    tool=tool_name,
                    purpose=purpose,
                    status="failure",
                    started_at=timestamp,
                    duration_ms=max(1, int((time.perf_counter() - started) * 1000)),
                    fields=_selected_fields(payload),
                    fact_ids=fact_ids or [],
                    adapter=adapter_name,
                    budget_remaining=self.budget_remaining,
                    result_summary="The source search failed.",
                    error=str(exc),
                )
            )
            raise
        self.trace.append(
            TraceEvent(
                id=f"trace_{uuid4().hex[:10]}",
                submission_id=submission_id,
                tool=tool_name,
                purpose=purpose,
                status="success",
                started_at=timestamp,
                duration_ms=max(1, int((time.perf_counter() - started) * 1000)),
                fields=_selected_fields(payload),
                fact_ids=fact_ids or [],
                adapter=adapter_name,
                budget_remaining=self.budget_remaining,
                result_summary=_result_summary(action, result),
            )
        )
        return result


def _selected_fields(payload: dict[str, Any] | None) -> list[str]:
    if not payload:
        return []
    select = payload.get("select", [])
    if isinstance(select, list):
        return [str(item) for item in select if isinstance(item, str)]
    if isinstance(select, dict):
        return [str(item) for item in select]
    return []


def _result_summary(action: str, result: Any) -> str:
    if action == "schema":
        registry = SchemaRegistry(result)
        count = len(registry.resources)
        noun = "source" if count == 1 else "sources"
        return f"The search found {count} {noun} of underwriting evidence."
    count, _ = _result_counts(result)
    if count == 0:
        return "The search returned no records."
    noun = "record" if count == 1 else "records"
    return f"The search returned {count} {noun}."


def _pagination(payload: dict[str, Any]) -> dict[str, int]:
    pagination = payload.get("pagination")
    if not isinstance(pagination, dict):
        return {}
    return {
        key: value
        for key in ("limit", "offset")
        if isinstance((value := pagination.get(key)), int)
    }


def _result_counts(result: Any) -> tuple[int, int | None]:
    if isinstance(result, list):
        return len(result), len(result)
    if not isinstance(result, dict):
        return 0, None
    total = result.get("total")
    rendered_total = total if isinstance(total, int) else None
    for key in ("records", "results", "items", "groups"):
        value = result.get(key)
        if isinstance(value, list):
            return len(value), rendered_total
    nested = result.get("data")
    if nested is not None and nested is not result:
        count, nested_total = _result_counts(nested)
        return count, rendered_total if rendered_total is not None else nested_total
    return (1 if "id" in result or "_id" in result else 0), rendered_total


def _failed_field_path(exc: Exception) -> str | None:
    if isinstance(exc, QueryValidationError) and exc.field_path:
        return exc.field_path
    match = re.search(
        r'(?:Unknown field in path|Unknown (?:selected )?field)\s+"([^"]+)"',
        str(exc),
        re.IGNORECASE,
    )
    return match.group(1) if match else None
