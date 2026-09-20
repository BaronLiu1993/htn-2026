from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
from .evidence_search import EvidenceSearch
from uuid import uuid4

from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field

from .config import Settings
from .adapters import DemoFederatoAdapter, FederatoAdapter
from .guideline_registry import GuidelinePackage
from .models import Assessment, EvidenceLedger, TraceEvent
from .profile_registry import InvestigationProfile
from .schema_registry import QueryValidationError, SchemaRegistry
from .tool_gateway import ToolGateway


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
        "name": "get_guideline",
        "description": (
            "Inspect the selected versioned guideline package. Its deterministic rules are "
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
                    "description": (
                        "One plain-English sentence for an underwriter. Name the evidence, the "
                        "guideline criterion it supports, and why the check matters."
                    ),
                },
                "query_json": {
                    "type": "string",
                    "description": "A JSON-encoded Federato query object.",
                },
                "fact_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Unresolved canonical fact IDs this query can resolve.",
                },
            },
            "required": ["purpose", "query_json", "fact_ids"],
            "additionalProperties": False,
        },
    },
]


SYSTEM_INSTRUCTIONS = """You are UnderwriteIQ's evidence-planning underwriting agent.

Your job is to resolve canonical facts before deterministic evaluation. You receive the selected guideline, investigation profile, current evidence ledger, live schema digest, and remaining budget. Inspect the runtime schema and guideline before querying. Every query must name the unresolved fact IDs it can resolve. Construct queries dynamically from schema results; never invent resources or fields.

Every query purpose is shown directly to an underwriter. Write it as a short business explanation, for example: "Check five-year incurred losses because the guideline requires total losses below $100,000." Do not mention JSON, schemas, tool calls, canonical IDs, planning turns, or implementation details in that purpose.

Query syntax is exact: the expand stage is the reference tree itself; never add another "expand" wrapper inside that tree. Sort entries are {"field":"name","direction":"asc"}. Select is either a list of field paths or a nested projection object. For broad queue evidence, prefer a direct query of a discovered resource with IDs in $in, then expand only references declared on that resource.

The deterministic evaluator runs after evidence gathering and is authoritative. You cannot change rule outcomes. Preserve missing, conflicting, ambiguous, and unavailable facts. Do not claim external enrichment was performed unless a tool returned it.

Grounding is strict: for each explanation, evidence_ids must be a subset of that same submission's allowed_evidence_ids. Copy those IDs exactly. A related record ID returned by a query is not an allowed citation unless that exact ID also appears in allowed_evidence_ids. Tool results may inform the prose, but they do not expand the citation allowlist.

The initial message includes the live schema and complete selected guideline. You can inspect them again if useful. Query in batches across the complete queue. Retain id and relationship fields on every returned record, including expanded records, so evidence can be attributed. Do not aggregate or rename fields: the ledger computes the guideline aggregates from source observations. Retrieve the next page when a page is full. After each query, use the updated ledger coverage to choose the next useful search. Stop when all facts are verified, no useful search remains, or the remaining budget is zero. The final report summarizes the search; return explanations as an empty list because explanations are generated from final deterministic outcomes afterward. Prefer a broad queue query followed by a focused query only when the first result shows missing or contradictory evidence. Keep each query purpose concise. Do not draft submission decisions before the deterministic evaluation. Do not reveal hidden chain-of-thought; provide only concise decision and query rationale summaries.
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


def _ledger_input(ledger: EvidenceLedger) -> dict[str, Any]:
    return {
        "submission_id": ledger.submission_id,
        "facts": [fact.model_dump(mode="json") for fact in ledger.facts],
        "allowed_evidence_ids": sorted(
            {
                observation.record_id
                for fact in ledger.facts
                for observation in fact.observations
            }
        ),
    }


def _guideline_payload(guideline: GuidelinePackage) -> dict[str, Any]:
    return guideline.model_dump(mode="json")


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


def _bounded_tool_result(value: Any, max_chars: int = 300_000) -> str:
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
    query_results: list[dict[str, Any]] = field(default_factory=list)


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
        search: EvidenceSearch,
        assessments: list[Assessment] | None = None,
        ledgers: list[EvidenceLedger] | None = None,
        registry: SchemaRegistry,
        guideline: GuidelinePackage,
        profile: InvestigationProfile | None = None,
        gateway: ToolGateway | None = None,
        federato: Any = None,
        mode: str,
        trace: list[TraceEvent],
    ) -> AgentRunResult:
        selected_guideline = guideline
        if gateway is None:
            if mode != "demo" and federato is None:
                raise ValueError("A gateway or source adapter is required for live mode.")
            adapter = DemoFederatoAdapter() if mode == "demo" else FederatoAdapter(federato)
            gateway = ToolGateway([adapter], selected_guideline.tool_policy, trace)
            gateway.registry = registry
        inputs = (
            [_assessment_input(item) for item in assessments]
            if assessments is not None
            else [_ledger_input(item) for item in ledgers or []]
        )
        prompt = {
            "goal": (
                "Resolve useful evidence gaps before deterministic evaluation. Summarize the search."
            ),
            "schema": registry.compact_digest(),
            "selected_guideline": _guideline_payload(guideline),
            "mode": mode,
            "guideline": {
                "id": selected_guideline.id,
                "version": selected_guideline.version,
            },
            "reference_library": [profile.model_dump(mode="json")] if profile else [],
            "remaining_tool_budget": gateway.budget_remaining,
            "submission_count": len(inputs),
            "submissions": inputs,
        }
        input_items: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": json.dumps(prompt, separators=(",", ":")),
            }
        ]
        called_tools: set[str] = {"inspect_schema", "get_guideline"}
        query_calls = 0
        total_tool_calls = 0
        query_results: list[dict[str, Any]] = []
        response_schema = AgentReport.model_json_schema()

        for turn in range(self.settings.openai_max_turns):
            guideline_called = "get_guideline" in called_tools
            force_report = (
                turn == self.settings.openai_max_turns - 1
                or query_calls >= self.settings.openai_max_query_calls
                or gateway.budget_remaining == 0
                or search.coverage()["unresolved_fact_count"] == 0
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
                    purpose="Decide which underwriting evidence to check next",
                    status="success",
                    started_at=datetime.now(),
                    duration_ms=max(1, int((time.perf_counter() - started) * 1000)),
                    result_summary=(
                        "The agent identified another evidence check that could resolve a "
                        "guideline question"
                        if calls
                        else "The agent completed its evidence review and prepared submission explanations"
                    ),
                )
            )

            if not calls:
                missing_tools = {"inspect_schema", "query_federato"} - called_tools
                if not guideline_called:
                    missing_tools.add("get_guideline")
                if missing_tools and not force_report and turn + 1 < self.settings.openai_max_turns:
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
                    query_results=query_results,
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
                    elif name == "get_guideline":
                        result = {"ok": True, "guideline": _guideline_payload(selected_guideline)}
                    elif name == "query_federato":
                        query_calls += 1
                        result = await self._query_tool(
                            arguments=arguments,
                            registry=registry,
                            gateway=gateway,
                            trace=trace,
                            query_calls=query_calls,
                        )
                        if result.get("ok"):
                            query_results.append(result)
                            feedback = search.apply(result)
                            result = {"ok": True, "query": result["query"], **feedback,
                                      "remaining_query_budget": min(gateway.budget_remaining, self.settings.openai_max_query_calls - query_calls)}
                            trace[-1].result_summary = (
                                f"Found {feedback['records_found']} related records. "
                                f"Updated {feedback['useful_fact_changes']} underwriting answers."
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
        gateway: ToolGateway,
        trace: list[TraceEvent],
        query_calls: int,
    ) -> dict[str, Any]:
        purpose = str(arguments.get("purpose") or "Inspect underwriting evidence")[:240]
        fact_ids = [str(item) for item in arguments.get("fact_ids", [])][:40]
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
            query.setdefault("pagination", {"limit": 100, "offset": 0})
            registry.validate_query(query)
            result = await gateway.query(query, purpose=purpose, fact_ids=fact_ids)
            return {
                "ok": True,
                "query": query,
                "fact_ids": fact_ids,
                "result": result,
            }
        except (json.JSONDecodeError, QueryValidationError) as exc:
            trace.append(
                TraceEvent(
                    id=f"trace_{uuid4().hex[:10]}",
                    tool="query_guidance",
                    purpose="Refine an evidence search that did not match the available data",
                    status="failure",
                    started_at=datetime.now(),
                    duration_ms=max(1, int((time.perf_counter() - started) * 1000)),
                    result_summary="The proposed search used unavailable fields, so the agent must revise it.",
                    fact_ids=fact_ids,
                    adapter="federato",
                    budget_remaining=gateway.budget_remaining,
                    error=str(exc),
                )
            )
            return {"ok": False, "error": str(exc), "repairable": True}
        except Exception as exc:
            return {"ok": False, "error": "Federato query failed safely.", "repairable": True}


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
