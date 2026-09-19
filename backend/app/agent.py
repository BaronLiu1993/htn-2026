from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import uuid4

from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field

from .appetite_loader import AppetitePack
from .config import Settings
from .demo_federato import query_demo
from .federato_client import FederatoClient
from .models import Assessment, TraceEvent
from .schema_registry import QueryValidationError, SchemaRegistry


class AgentExplanation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    submission_id: str
    explanation: str
    key_factors: list[str]
    contradictions: list[str]
    missing_information: list[str]
    evidence_ids: list[str] = Field(
        description=(
            "Evidence record IDs copied only from the matching assessment's "
            "allowed_evidence_ids list. Do not substitute related tool-result IDs."
        )
    )


class AgentReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_summary: str
    evidence_strategy: list[str]
    adaptations: list[str]
    limitations: list[str]
    explanations: list[AgentExplanation]


class ResponsesTransport(Protocol):
    async def create(self, payload: dict[str, Any]) -> dict[str, Any]: ...


class OpenAIResponsesTransport:
    def __init__(self, settings: Settings) -> None:
        self.client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            timeout=settings.openai_request_timeout_seconds,
            max_retries=1,
        )

    async def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self.client.responses.create(**payload)
        return response.model_dump(mode="json")


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "inspect_schema",
        "description": (
            "Inspect the runtime Federato schema before constructing queries. Returns resources, "
            "fields, types, references, and cardinalities."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_appetite",
        "description": (
            "Inspect the active versioned carrier appetite. The returned hard requirements are "
            "authoritative and cannot be overridden by model judgment."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "query_federato",
        "description": (
            "Execute one schema-valid Federato query for a stated evidence purpose. query_json "
            "must be a JSON object using the documented resource/where/expand/unwind/filter/over/"
            "select/sort/pagination pipeline. Use $elemMatch at array boundaries and expansion for "
            "references. Results are bounded."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "purpose": {
                    "type": "string",
                    "description": "Concise underwriting reason for requesting this evidence.",
                },
                "query_json": {
                    "type": "string",
                    "description": "A JSON-encoded Federato query object.",
                },
            },
            "required": ["purpose", "query_json"],
            "additionalProperties": False,
        },
    },
]


SYSTEM_INSTRUCTIONS = """You are UnderwriteIQ's evidence-planning underwriting agent.

Your job is to reason about what Federato evidence is needed to verify already-computed carrier-appetite outcomes. You must inspect the runtime schema and active appetite before querying. Construct queries dynamically from those tool results; never invent resources or fields. Use where before expansion, filter after expansion, $elemMatch at array boundaries, and expansion when a reference must be hydrated.

Query syntax is exact: the expand stage is the reference tree itself, for example {"expand":{"policy":{"buildings":true,"claims":true}}}; never add another "expand" wrapper inside that tree. Sort entries are {"field":"premium","direction":"desc"}. Select is either a list of field paths or a nested projection object. For broad queue evidence, prefer a direct query of a discovered resource with IDs in $in, then expand only references declared on that resource.

The deterministic appetite evaluator is authoritative. Never alter a status, target-match count, rule result, or recommendation. Your explanations may only summarize the supplied assessment facts and evidence returned by tools. Transparently identify missing data and contradictions. Do not claim external enrichment was performed unless a tool returned it.

Grounding is strict: for each explanation, evidence_ids must be a subset of that same assessment's allowed_evidence_ids. Copy those IDs exactly. A related Policy, Building, or Claim ID returned by a query is not an allowed citation unless that exact ID also appears in allowed_evidence_ids. Tool results may inform the prose, but they do not expand the citation allowlist.

Before finishing, call inspect_schema, get_appetite, and query_federato at least once. Prefer a broad queue query followed by a focused query only when the first result shows missing or contradictory evidence. Keep each query purpose concise. Return a short, underwriter-friendly explanation for every supplied assessment. Do not reveal hidden chain-of-thought; provide only concise decision and query rationale summaries.
"""


def _assessment_input(assessment: Assessment) -> dict[str, Any]:
    return {
        "submission_id": assessment.submission_id,
        "submission_number": assessment.submission_number,
        "insured_name": assessment.insured_name,
        "status": assessment.status,
        "target_matches": assessment.target_matches,
        "target_preferences_total": assessment.target_preferences_total,
        "evidence_completeness": assessment.evidence_completeness,
        "passed_requirements": [item.name for item in assessment.passed_requirements],
        "failed_requirements": [item.name for item in assessment.failed_requirements],
        "unresolved_rules": [item.name for item in assessment.unresolved_rules],
        "matched_preferences": [item.name for item in assessment.matched_preferences],
        "missing_information": assessment.missing_information,
        "recommended_action": assessment.recommended_action,
        "allowed_evidence_ids": sorted(
            {item.record_id for item in assessment.evidence}
        ),
    }


def _appetite_payload(appetite: AppetitePack) -> dict[str, Any]:
    return {
        "id": appetite.id,
        "version": appetite.version,
        "effective_from": appetite.effective_from.isoformat(),
        "requirements": [rule.model_dump(mode="json") for rule in appetite.requirements],
        "preferences": [rule.model_dump(mode="json") for rule in appetite.preferences],
    }


def _output_text(response: dict[str, Any]) -> str:
    direct = response.get("output_text")
    if isinstance(direct, str) and direct:
        return direct
    for item in response.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"} and isinstance(content.get("text"), str):
                return content["text"]
    return ""


def _bounded_tool_result(value: Any, max_chars: int = 18_000) -> str:
    encoded = json.dumps(value, default=str, separators=(",", ":"))
    if len(encoded) <= max_chars:
        return encoded
    return json.dumps(
        {
            "truncated": True,
            "summary": encoded[:max_chars],
            "note": "Tool result was truncated to keep the agent context bounded.",
        },
        separators=(",", ":"),
    )


def _function_call_input(item: dict[str, Any]) -> dict[str, Any]:
    """Convert an output function call into the minimal valid Responses input item.

    Response objects contain server-owned fields such as ``id`` and ``status``. Passing
    their full serialized form back through ``input`` causes the API to reject the next
    turn, so only the documented function-call fields are retained.
    """
    return {
        "type": "function_call",
        "call_id": item.get("call_id"),
        "name": item.get("name"),
        "arguments": item.get("arguments") or "{}",
    }


@dataclass
class AgentRunResult:
    report: AgentReport
    model: str
    tool_calls: int


class UnderwritingAgent:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: ResponsesTransport | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport or OpenAIResponsesTransport(settings)

    async def run(
        self,
        *,
        assessments: list[Assessment],
        registry: SchemaRegistry,
        appetite: AppetitePack,
        federato: FederatoClient,
        mode: str,
        trace: list[TraceEvent],
    ) -> AgentRunResult:
        prompt = {
            "goal": (
                "Verify the queue's appetite evidence, adapt queries if needed, and explain every "
                "deterministic decision in plain English."
            ),
            "mode": mode,
            "assessment_count": len(assessments),
            "assessments": [_assessment_input(item) for item in assessments],
        }
        input_items: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": json.dumps(prompt, separators=(",", ":")),
            }
        ]
        called_tools: set[str] = set()
        query_calls = 0
        total_tool_calls = 0
        response_schema = AgentReport.model_json_schema()

        for turn in range(self.settings.openai_max_turns):
            required_tools_called = {
                "inspect_schema",
                "get_appetite",
                "query_federato",
            }.issubset(called_tools)
            force_report = (
                turn == self.settings.openai_max_turns - 1 and required_tools_called
            )
            if force_report:
                input_items.append(
                    {
                        "role": "user",
                        "content": (
                            "The bounded evidence-gathering phase is complete. Produce the final "
                            "structured underwriting report now without requesting another tool."
                        ),
                    }
                )
            started = time.perf_counter()
            payload: dict[str, Any] = {
                "model": self.settings.openai_model,
                "instructions": SYSTEM_INSTRUCTIONS,
                "input": input_items,
                "store": False,
                "reasoning": {"effort": self.settings.openai_reasoning_effort},
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "underwriting_agent_report",
                        "strict": True,
                        "schema": response_schema,
                    }
                },
            }
            if not force_report:
                payload["tools"] = TOOL_DEFINITIONS
                payload["parallel_tool_calls"] = False
            response = await self.transport.create(payload)
            output = response.get("output", [])
            calls = [item for item in output if item.get("type") == "function_call"]
            trace.append(
                TraceEvent(
                    id=f"trace_{uuid4().hex[:10]}",
                    tool="openai_agent",
                    purpose=f"Agent planning turn {turn + 1}",
                    status="success",
                    started_at=datetime.now(),
                    duration_ms=max(1, int((time.perf_counter() - started) * 1000)),
                    result_summary=(
                        f"Model requested {len(calls)} tool call(s)"
                        if calls
                        else "Model produced a structured underwriting report"
                    ),
                )
            )

            if not calls:
                missing_tools = {"inspect_schema", "get_appetite", "query_federato"} - called_tools
                if missing_tools and turn + 1 < self.settings.openai_max_turns:
                    input_items.append(
                        {
                            "role": "user",
                            "content": (
                                "Required evidence steps remain. Call these tools before finishing: "
                                + ", ".join(sorted(missing_tools))
                            ),
                        }
                    )
                    continue
                text = _output_text(response)
                if not text:
                    raise RuntimeError("OpenAI returned no structured agent report.")
                report = AgentReport.model_validate_json(text)
                return AgentRunResult(
                    report=report,
                    model=str(response.get("model") or self.settings.openai_model),
                    tool_calls=total_tool_calls,
                )

            input_items.extend(_function_call_input(call) for call in calls)
            for call in calls:
                total_tool_calls += 1
                name = str(call.get("name"))
                called_tools.add(name)
                try:
                    arguments = json.loads(call.get("arguments") or "{}")
                except json.JSONDecodeError as exc:
                    result: Any = {"ok": False, "error": f"Invalid tool arguments: {exc}"}
                else:
                    if name == "inspect_schema":
                        result = {"ok": True, **registry.compact_digest()}
                    elif name == "get_appetite":
                        result = {"ok": True, "appetite": _appetite_payload(appetite)}
                    elif name == "query_federato":
                        query_calls += 1
                        result = await self._query_tool(
                            arguments=arguments,
                            registry=registry,
                            federato=federato,
                            mode=mode,
                            trace=trace,
                            query_calls=query_calls,
                        )
                    else:
                        result = {"ok": False, "error": f'Unknown tool "{name}".'}
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.get("call_id"),
                        "output": _bounded_tool_result(result),
                    }
                )
        raise RuntimeError("OpenAI agent exceeded its bounded turn limit.")

    async def _query_tool(
        self,
        *,
        arguments: dict[str, Any],
        registry: SchemaRegistry,
        federato: FederatoClient,
        mode: str,
        trace: list[TraceEvent],
        query_calls: int,
    ) -> dict[str, Any]:
        purpose = str(arguments.get("purpose") or "Inspect underwriting evidence")[:240]
        started = time.perf_counter()
        if query_calls > self.settings.openai_max_query_calls:
            return {
                "ok": False,
                "error": "The bounded Federato query-call limit has been reached.",
            }
        try:
            query = json.loads(str(arguments.get("query_json") or ""))
            if not isinstance(query, dict):
                raise QueryValidationError("Query must decode to a JSON object.")
            query.setdefault("pagination", {"limit": 25, "offset": 0})
            registry.validate_query(query)
            result = query_demo(query) if mode == "demo" else await federato.query(query)
            summary = _query_summary(result)
            trace.append(
                TraceEvent(
                    id=f"trace_{uuid4().hex[:10]}",
                    tool="agent_query_federato",
                    purpose=purpose,
                    status="success",
                    started_at=datetime.now(),
                    duration_ms=max(1, int((time.perf_counter() - started) * 1000)),
                    fields=_selected_fields(query),
                    result_summary=summary,
                )
            )
            return {"ok": True, "query": query, "result": result}
        except (json.JSONDecodeError, QueryValidationError) as exc:
            trace.append(
                TraceEvent(
                    id=f"trace_{uuid4().hex[:10]}",
                    tool="agent_query_federato",
                    purpose=purpose,
                    status="failure",
                    started_at=datetime.now(),
                    duration_ms=max(1, int((time.perf_counter() - started) * 1000)),
                    result_summary="Rejected an invalid agent-generated query",
                    error=str(exc),
                )
            )
            return {"ok": False, "error": str(exc), "repairable": True}
        except Exception as exc:
            trace.append(
                TraceEvent(
                    id=f"trace_{uuid4().hex[:10]}",
                    tool="agent_query_federato",
                    purpose=purpose,
                    status="failure",
                    started_at=datetime.now(),
                    duration_ms=max(1, int((time.perf_counter() - started) * 1000)),
                    result_summary="Federato query failed",
                    error=str(exc),
                )
            )
            return {"ok": False, "error": "Federato query failed safely.", "repairable": True}


def _selected_fields(query: dict[str, Any]) -> list[str]:
    select = query.get("select")
    if isinstance(select, list):
        return [item for item in select if isinstance(item, str)][:40]
    if isinstance(select, dict):
        return list(select)[:40]
    return []


def _query_summary(result: Any) -> str:
    if isinstance(result, dict):
        total = result.get("total")
        resource = result.get("resource", "records")
        if isinstance(total, int):
            return f"Retrieved {total} matching {resource} records"
        for key in ("records", "results", "items", "groups", "data"):
            if isinstance(result.get(key), list):
                return f"Retrieved {len(result[key])} {resource} result rows"
    if isinstance(result, list):
        return f"Retrieved {len(result)} result rows"
    return "Federato query completed"


def apply_agent_report(
    assessments: list[Assessment], report: AgentReport
) -> tuple[int, list[str]]:
    by_id = {item.submission_id: item for item in assessments}
    applied = 0
    warnings: list[str] = []
    for explanation in report.explanations:
        assessment = by_id.get(explanation.submission_id)
        if assessment is None:
            warnings.append(
                f"Ignored an AI explanation for unknown submission {explanation.submission_id}."
            )
            continue
        valid_evidence_ids = {item.record_id for item in assessment.evidence}
        unsupported = set(explanation.evidence_ids) - valid_evidence_ids
        if unsupported:
            warnings.append(
                f"Ignored unsupported evidence references for {explanation.submission_id}: "
                + ", ".join(sorted(unsupported))
            )
            continue
        if explanation.explanation.strip():
            assessment.explanation = explanation.explanation.strip()
            assessment.explanation_source = "openai"
            assessment.missing_information = list(
                dict.fromkeys(
                    assessment.missing_information
                    + [
                        item.strip()
                        for item in explanation.missing_information
                        if item.strip()
                    ]
                )
            )
            assessment.warnings = list(
                dict.fromkeys(
                    assessment.warnings
                    + [
                        item.strip()
                        for item in explanation.contradictions
                        if item.strip()
                    ]
                )
            )
            applied += 1
    return applied, warnings
