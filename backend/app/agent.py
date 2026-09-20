from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
from .evidence_search import EvidenceSearch, _headquarters_note, _is_headquarters_query
from uuid import uuid4

from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field

from .config import Settings
from .adapters import DemoFederatoAdapter, FederatoAdapter
from .evidence_ledger import validate_binding
from .federato_client import FederatoError
from .guideline_registry import FactBinding, GuidelinePackage
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
        "name": "bind_facts",
        "description": (
            "Propose a schema-valid runtime binding from a canonical guideline fact to "
            "discovered resources, fields, relationship paths, and one approved "
            "deterministic operation. Bindings cannot change rule operators or thresholds."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "bindings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "fact_id": {"type": "string"},
                            "resource": {"type": "string"},
                            "operation": {
                                "type": "string",
                                "enum": [
                                    "scalar",
                                    "minimum",
                                    "sum",
                                    "weighted_match_share",
                                    "rolling_sum",
                                    "rolling_component_sum",
                                ],
                            },
                            "path": {"type": "string"},
                            "field": {"type": "string"},
                            "fields": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "date_field": {"type": "string"},
                            "collection": {"type": "string"},
                            "relationship_path": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": [
                            "fact_id",
                            "resource",
                            "operation",
                            "path",
                            "field",
                            "fields",
                            "date_field",
                            "collection",
                            "relationship_path",
                        ],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["bindings"],
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
                        "One short sentence for an underwriter. Name the evidence and why it matters."
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

Resolve canonical guideline facts before deterministic evaluation. You receive the selected guideline, a compact live schema with identifier types, native candidate IDs, discovered relationship IDs, unresolved underwriting questions, pagination, and remaining budget.

Work in this order:
1. Inspect the runtime schema. Do not invent resources or fields. Identifier types are binding: if a field is number, $in and $eq values must be JSON numbers, not quoted strings.
2. Bind each unresolved canonical fact to discovered resources, relationship paths, fields, and one approved deterministic operation. Bindings cannot change rule operators or thresholds.
3. Write one Federato query for a named underwriting goal. Translate the goal into the documented pipeline. Do not reuse a canned Policy expand payload.

Query language:
- Pipeline: where (raw records) → expand → unwind → filter (hydrated rows) → over → select → sort → pagination.
- Prefer expand when later filter or select must traverse a reference. Use select-leaf $expand when the hydrated object is only needed in the reply.
- Use $elemMatch at array boundaries such as exposure_units, buildings, and claims. Never write a bare path like locations.state across an array.
- Filter in-scope work with native IDs on the relationship field (for example Policy.submission), not stringified id unless that schema field is a string.
- Do not invent Policy.tiv if the digest only exposes building TIV. Do not $sum in the query unless the ledger cannot compute it from source rows. The ledger computes aggregates from source observations.
- Retain each resource's declared identifier and relationship fields on every returned record, including expanded records.
- Paginate with limit 1..100. Retrieve the next page when a candidate query is full.

After each result, read search_note. If it reports zero rows, the next query must change: identifier types, where versus filter, expand, relationship field, or pagination. If rows return but total insured value, risk state, or insured name stay missing, expand the declared reference path instead of repeating the same select. Do not scan the complete source queue or unrelated business lines.

Every query purpose is shown to an underwriter. Write one short sentence. Name the evidence and why it matters. Example: "Need the total insured value for the commercial property submissions." Do not mention JSON, schemas, tool calls, canonical IDs, planning turns, or implementation details.

If insured name, risk state, building TIV, year built, or construction remain missing after a Policy search, the next query must change path. Expand Submission.insured.hq.buildings for those candidate ids even when a Policy exists. Premium, business type, and claims still require the linked Policy.

The deterministic evaluator is authoritative. Preserve missing, conflicting, ambiguous, and unavailable facts. Do not claim external enrichment was performed unless a tool returned it. Grounding is strict: evidence_ids must be copied from that submission's allowed_evidence_ids. The final report summarizes the search; return explanations as an empty list because explanations are generated from final deterministic outcomes afterward. Stop when all facts are verified, no useful candidate search remains, or the remaining budget is zero. Do not draft submission decisions before deterministic evaluation. Do not reveal hidden chain-of-thought; provide only concise decision and query rationale summaries.
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


def _unsearched_evidence_resources(
    search: EvidenceSearch,
    guideline: GuidelinePackage,
) -> list[str]:
    unresolved_fact_ids = {
        fact.fact_id
        for ledger in search.ledgers
        for fact in ledger.facts
        if fact.state != "verified"
    }
    searched = set(search.coverage()["pagination_state"])
    return sorted(
        {
            binding.resource
            for definition in guideline.required_facts
            if definition.id in unresolved_fact_ids
            and (binding := search.mapper.bindings.get(definition.id)) is not None
            and binding.status == "bound"
            and binding.resource not in searched
        }
    )


def _resource_phrase(resource: str, count: int) -> str:
    key = "".join(character for character in resource.lower() if character.isalnum())
    names = {
        "policy": ("policy", "policies"),
        "submission": ("submission", "submissions"),
        "building": ("building", "buildings"),
        "claim": ("claim", "claims"),
        "location": ("location", "locations"),
        "insured": ("insured record", "insured records"),
        "exposureunit": ("exposure unit", "exposure units"),
    }
    singular, plural = names.get(key, (f"{resource.lower()} record", f"{resource.lower()} records"))
    return singular if count == 1 else plural


def _search_note(
    *,
    query: dict[str, Any],
    feedback: dict[str, Any],
) -> str:
    if _is_headquarters_query(query):
        return _headquarters_note(feedback)
    resource = str(query.get("resource") or "source")
    records = int(feedback.get("records_found") or 0)
    noun = _resource_phrase(resource, records if records else 2)
    effect = str(feedback.get("query_effect") or "")
    unresolved = [
        item
        for item in feedback.get("open_questions") or []
        if int(item.get("unresolved_accounts") or 0) > 0
    ]
    resolved_labels = []
    for item in feedback.get("newly_resolved_facts") or []:
        fact_id = str(item.get("fact_id") or "")
        if fact_id and fact_id not in resolved_labels:
            resolved_labels.append(fact_id)
    questions = {
        str(fact.get("fact_id")): str(fact.get("question") or fact.get("fact_id"))
        for item in feedback.get("submissions") or []
        for fact in item.get("facts") or []
    }
    resolved_text = ", ".join(
        questions.get(item, item).lower() for item in resolved_labels[:3]
    )
    no_policy = int(feedback.get("submissions_without_policy_count") or 0)
    resource_key = "".join(
        character for character in resource.lower() if character.isalnum()
    )
    if effect == "zero_rows" or records == 0:
        return (
            f"The search returned no {noun}. "
            "The next search must use a different filter."
        )
    if effect == "unowned_rows":
        return (
            f"The search returned {records} {noun}. "
            "These records do not belong to the selected submissions."
        )
    if effect == "unrelated_rows":
        return (
            f"The search returned {records} {noun} for other business. "
            "The next search must use the in-scope submissions."
        )
    if no_policy and resource_key == "policy":
        return (
            f"The search returned {records} {noun}. "
            f"{no_policy} submissions have no Policy."
        )
    missing = ""
    if unresolved:
        top = unresolved[0]
        missing = (
            f"{int(top['unresolved_accounts'])} submissions still have no "
            f"{str(top['question']).lower()}."
        )
    if effect in {"repeated_rows", "no_fact_change"}:
        opener = (
            f"The search returned {records} {noun} already on file."
            if effect == "repeated_rows"
            else f"The search returned {records} {noun}."
        )
        return f"{opener} {missing or 'No new facts were confirmed.'}".strip()
    accounts = int(feedback.get("affected_submissions") or 0)
    if resolved_text:
        verb = "is" if len(resolved_labels) == 1 else "are"
        counted = f"{accounts} submission" if accounts == 1 else f"{accounts} submissions"
        return (
            f"The search returned {records} {noun}. "
            f"{resolved_text} {verb} now on {counted}."
        )
    if missing:
        return f"The search returned {records} {noun}. {missing}"
    return f"The search returned {records} {noun}. Underwriting facts are now on file."


def _apply_fact_bindings(
    *,
    arguments: dict[str, Any],
    search: EvidenceSearch,
    registry: SchemaRegistry,
    guideline: GuidelinePackage,
    trace: list[TraceEvent],
) -> dict[str, Any]:
    proposed: list[FactBinding] = []
    rejected: list[str] = []
    for item in arguments.get("bindings") or []:
        if not isinstance(item, dict):
            continue
        payload = {
            key: (None if value == "" else value)
            for key, value in item.items()
        }
        fields = [str(field) for field in payload.get("fields") or [] if field]
        try:
            proposed.append(
                FactBinding(
                    fact_id=str(payload.get("fact_id") or ""),
                    resource=str(payload.get("resource") or ""),
                    operation=str(payload.get("operation") or "scalar"),
                    path=payload.get("path"),
                    collection=payload.get("collection"),
                    field=payload.get("field"),
                    fields=fields,
                    date_field=payload.get("date_field"),
                    relationship_path=[
                        str(path)
                        for path in payload.get("relationship_path") or []
                        if path
                    ],
                )
            )
        except Exception as exc:
            rejected.append(str(exc))
    rebound = search.rebind(proposed)
    bound = rebound["bound_fact_ids"]
    unbound = rebound["unbound_fact_ids"]
    validated = [
        validate_binding(item, guideline, registry)
        for item in proposed
    ]
    rejected.extend(
        item.reason or item.fact_id
        for item in validated
        if item.status != "bound"
    )
    trace.append(
        TraceEvent(
            id=f"trace_{uuid4().hex[:10]}",
            tool="bind_facts",
            purpose="Link each guideline question to a source field.",
            status="success" if bound else "failure",
            started_at=datetime.now(),
            duration_ms=1,
            fact_ids=bound,
            result_summary=(
                f"The system linked {len(bound)} guideline questions to source fields."
                + (
                    f" {len(unbound)} questions have no source field."
                    if unbound
                    else ""
                )
            ),
            error="; ".join(rejected) if rejected else None,
        )
    )
    return {"ok": True, **rebound, "rejected": rejected}


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
    stop_reason: str = "no_useful_search"
    unresolved_facts_by_reason: dict[str, int] = field(default_factory=dict)
    model_latency_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0


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
        coverage = search.coverage()
        prompt = {
            "goal": (
                "Resolve useful evidence gaps before deterministic evaluation. "
                "Inspect the live schema, bind facts, then write one query for a named underwriting goal."
            ),
            "schema": registry.compact_digest(),
            "identifier_types": registry.identifier_types(),
            "selected_guideline": _guideline_payload(guideline),
            "mode": mode,
            "guideline": {
                "id": selected_guideline.id,
                "version": selected_guideline.version,
            },
            "reference_library": [profile.model_dump(mode="json")] if profile else [],
            "remaining_tool_budget": gateway.budget_remaining,
            "submission_count": len(inputs),
            "candidate_submission_ids": search.candidate_query_ids,
            "candidate_display_ids": sorted(search.candidate_ids),
            "relationship_ids": coverage["relationship_ids"],
            "open_questions": coverage.get("open_questions", []),
            "submissions_without_policy": coverage.get("submissions_without_policy", []),
            "unresolved_facts_by_submission": coverage["submissions"],
            "unresolved_facts_by_reason": coverage["unresolved_by_reason"],
            "pagination_state": coverage["pagination_state"],
            "fact_bindings": [
                search.mapper.bindings[fact.id].model_dump(mode="json")
                if fact.id in search.mapper.bindings
                else {
                    "fact_id": fact.id,
                    "status": "unbound",
                    "hint": fact.source.model_dump(mode="json"),
                }
                for fact in guideline.required_facts
            ],
            "approved_binding_operations": [
                "scalar",
                "minimum",
                "sum",
                "weighted_match_share",
                "rolling_sum",
                "rolling_component_sum",
            ],
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
        stop_reason = "no_useful_search"
        model_latency_ms = 0
        prompt_tokens = 0
        completion_tokens = 0

        for turn in range(self.settings.openai_max_turns):
            guideline_called = "get_guideline" in called_tools
            current_coverage = search.coverage()
            if current_coverage["unresolved_fact_count"] == 0:
                stop_reason = "all_facts_resolved"
            elif query_calls >= self.settings.openai_max_query_calls:
                stop_reason = "query_budget_exhausted"
            elif gateway.budget_remaining == 0:
                stop_reason = "tool_budget_exhausted"
            elif turn == self.settings.openai_max_turns - 1:
                stop_reason = "turn_limit_reached"
            force_report = (
                turn == self.settings.openai_max_turns - 1
                or query_calls >= self.settings.openai_max_query_calls
                or gateway.budget_remaining == 0
                or current_coverage["unresolved_fact_count"] == 0
            )
            if force_report:
                await search.fill_headquarters(gateway)
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
                needs_binding = any(
                    fact.id not in search.mapper.bindings
                    or search.mapper.bindings[fact.id].status != "bound"
                    for fact in selected_guideline.required_facts
                )
                payload["tools"] = [
                    tool
                    for tool in TOOL_DEFINITIONS
                    if tool["name"] == "query_federato"
                    or (tool["name"] == "bind_facts" and needs_binding)
                ]
                payload["parallel_tool_calls"] = False
            response = await self.transport.create(payload)
            elapsed_ms = max(1, int((time.perf_counter() - started) * 1000))
            model_latency_ms += elapsed_ms
            usage = response.get("usage") or {}
            prompt_tokens += int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
            completion_tokens += int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
            output = response.get("output", [])
            calls = [item for item in output if item.get("type") == "function_call"]
            trace.append(
                TraceEvent(
                    id=f"trace_{uuid4().hex[:10]}",
                    tool="openai_agent",
                    purpose="Choose the next evidence search.",
                    status="success",
                    started_at=datetime.now(),
                    duration_ms=elapsed_ms,
                    result_summary=(
                        "The next search can confirm a guideline fact."
                        if calls
                        else "Evidence search is complete."
                    ),
                )
            )

            if not calls:
                missing_tools = {"inspect_schema", "query_federato"} - called_tools
                if not guideline_called:
                    missing_tools.add("get_guideline")
                if any(
                    binding.status != "bound"
                    for binding in search.mapper.bindings.values()
                ) or any(
                    fact.id not in search.mapper.bindings
                    for fact in selected_guideline.required_facts
                ):
                    missing_tools.add("bind_facts")
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
                await search.fill_headquarters(gateway)
                unsearched = _unsearched_evidence_resources(
                    search,
                    selected_guideline,
                )
                if (
                    unsearched
                    and query_calls < self.settings.openai_max_query_calls
                    and gateway.budget_remaining > 0
                    and turn + 1 < self.settings.openai_max_turns
                ):
                    input_items.append(
                        {
                            "role": "user",
                            "content": (
                                "Unresolved guideline facts still have unsearched bound sources: "
                                + ", ".join(unsearched)
                                + ". Query the candidate-linked records before finishing."
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
                    stop_reason=stop_reason,
                    unresolved_facts_by_reason=search.coverage()[
                        "unresolved_by_reason"
                    ],
                    model_latency_ms=model_latency_ms,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
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
                        result = {
                            "ok": True,
                            **registry.compact_digest(),
                            "fact_bindings": [
                                search.mapper.bindings[fact.id].model_dump(mode="json")
                                if fact.id in search.mapper.bindings
                                else {"fact_id": fact.id, "status": "unbound"}
                                for fact in selected_guideline.required_facts
                            ],
                        }
                    elif name == "get_guideline":
                        result = {"ok": True, "guideline": _guideline_payload(selected_guideline)}
                    elif name == "bind_facts":
                        result = _apply_fact_bindings(
                            arguments=arguments,
                            search=search,
                            registry=registry,
                            guideline=selected_guideline,
                            trace=trace,
                        )
                    elif name == "query_federato":
                        query_calls += 1
                        result = await self._query_tool(
                            arguments=arguments,
                            search=search,
                            registry=registry,
                            gateway=gateway,
                            trace=trace,
                            query_calls=query_calls,
                        )
                        if result.get("ok"):
                            query_results.append(result)
                            feedback = search.apply(result)
                            gateway.annotate_query(
                                result.get("audit_id"),
                                {
                                    key: value
                                    for key, value in feedback.items()
                                    if key
                                    not in {
                                        "submissions",
                                        "relationship_ids",
                                        "pagination_state",
                                        "unresolved_by_reason",
                                    }
                                },
                            )
                            result = {"ok": True, "query": result["query"], **feedback,
                                      "search_note": _search_note(query=result["query"], feedback=feedback),
                                      "remaining_query_budget": min(gateway.budget_remaining, self.settings.openai_max_query_calls - query_calls)}
                            summary = result["search_note"]
                            trace[-1].result_summary = summary
                            trace[-1].records_inspected = int(feedback["records_found"])
                            trace[-1].facts_changed = int(feedback["useful_fact_changes"])
                            trace[-1].source_resource = str(result["query"].get("resource") or "")
                            trace[-1].fact_ids = [str(item) for item in (result.get("fact_ids") or arguments.get("fact_ids") or [])]
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
        search: EvidenceSearch,
        registry: SchemaRegistry,
        gateway: ToolGateway,
        trace: list[TraceEvent],
        query_calls: int,
    ) -> dict[str, Any]:
        purpose = str(arguments.get("purpose") or "Need underwriting evidence.")[:240]
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
            query = registry.coerce_query(query)
            query = search.prepare_query(query)
            result = await gateway.query(query, purpose=purpose, fact_ids=fact_ids)
            return {
                "ok": True,
                "query": query,
                "fact_ids": fact_ids,
                "result": result,
                "audit_id": gateway.last_query_audit_id,
            }
        except (json.JSONDecodeError, QueryValidationError, FederatoError) as exc:
            message = str(exc)
            repairable = not isinstance(exc, FederatoError) or any(
                token in message
                for token in (
                    "VALIDATION_ERROR",
                    "Unknown field",
                    "Invalid",
                    "NOT_FOUND",
                    "Unknown resource",
                )
            )
            if isinstance(exc, FederatoError) and not repairable:
                raise
            trace.append(
                TraceEvent(
                    id=f"trace_{uuid4().hex[:10]}",
                    tool="query_guidance",
                    purpose="Revise a search that did not match the source.",
                    status="failure",
                    started_at=datetime.now(),
                    duration_ms=max(1, int((time.perf_counter() - started) * 1000)),
                    result_summary="The search did not match the available fields. The next search must use a different path.",
                    fact_ids=fact_ids,
                    budget_remaining=gateway.budget_remaining,
                    error=message,
                )
            )
            return {"ok": False, "error": message, "repairable": True}


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
