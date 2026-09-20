from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any, Protocol
from uuid import uuid4

from .guideline_registry import ToolPolicy
from .models import TraceEvent
from .schema_registry import SchemaRegistry


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

    async def schema(self, *, purpose: str = "Discover source schema") -> Any:
        result = await self._execute("federato", "schema", None, purpose=purpose)
        self.registry = SchemaRegistry(result)
        return result

    async def query(
        self,
        payload: dict[str, Any],
        *,
        purpose: str = "Retrieve source evidence",
        fact_ids: list[str] | None = None,
        submission_id: str | None = None,
    ) -> Any:
        if self.registry is None:
            raise RuntimeError("Schema discovery must run before a source query.")
        self.registry.validate_query(payload)
        return await self._execute(
            "federato",
            "query",
            payload,
            purpose=purpose,
            fact_ids=fact_ids or [],
            submission_id=submission_id,
        )

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
                    result_summary=f"{adapter_name} {action} failed",
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
        return f"Found {len(registry.resources)} sources of underwriting evidence"
    if isinstance(result, list):
        return f"Retrieved {len(result)} records"
    if isinstance(result, dict):
        rows = result.get("data") or result.get("records") or result.get("results")
        if isinstance(rows, list):
            return f"Retrieved {len(rows)} records"
    return "Source query completed"
