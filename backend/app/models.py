from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


AssessmentStatus = Literal[
    "target", "acceptable", "needs_review", "out_of_appetite"
]
EvidenceState = Literal["verified", "missing", "conflicting", "ambiguous", "unavailable"]
RuleState = Literal["passed", "failed", "matched", "not_matched", "unresolved"]
TraceStatus = Literal["success", "retry", "failure", "cached"]
AgentMode = Literal["openai", "baseten", "openai_required", "baseten_required"]
ModelProvider = Literal["openai", "baseten"]


class BuildingEvidence(BaseModel):
    id: str
    year_built: int | None = None
    construction_type: str | None = None
    tiv: float | None = None
    occupancy: str | None = None
    sprinklered: bool | None = None
    protection_class: str | None = None
    flood_zone: str | None = None
    wildfire_score: float | None = None


class ClaimEvidence(BaseModel):
    id: str
    loss_date: date | None = None
    loss_value: float | None = None


class SubmissionEvidence(BaseModel):
    id: str
    submission_number: str
    insured_name: str
    received_date: date | None = None
    effective_date: date | None = None
    expiration_date: date | None = None
    submission_type: str | None = None
    line_of_business: str | None = None
    primary_state: str | None = None
    tiv: float | None = None
    premium: float | None = None
    buildings: list[BuildingEvidence] = Field(default_factory=list)
    claims: list[ClaimEvidence] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    raw_records: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    source_records: dict[str, list[str]] = Field(default_factory=dict)
    expected_related_records: dict[str, list[str]] = Field(default_factory=dict)


class EvidenceItem(BaseModel):
    resource: str
    record_id: str
    field: str
    value: Any
    label: str
    source_system: str = "federato"
    source_date: date | None = None
    observed_at: datetime | None = None
    fact_id: str | None = None
    state: EvidenceState = "verified"


class EvidenceObservation(BaseModel):
    source_system: str
    resource: str
    record_id: str
    field_path: str
    value: Any = None
    source_date: date | None = None
    retrieved_at: datetime
    state: EvidenceState = "verified"


class EvidenceFact(BaseModel):
    fact_id: str
    label: str
    value: Any = None
    state: EvidenceState
    requirement_ids: list[str] = Field(default_factory=list)
    observations: list[EvidenceObservation] = Field(default_factory=list)
    note: str | None = None


class EvidenceLedger(BaseModel):
    submission_id: str
    guideline_id: str
    guideline_version: str
    facts: list[EvidenceFact]

    def fact(self, fact_id: str) -> EvidenceFact | None:
        return next((item for item in self.facts if item.fact_id == fact_id), None)


class RuleOutcome(BaseModel):
    rule_id: str
    name: str
    kind: Literal["requirement", "preference"]
    state: RuleState
    actual_value: Any = None
    expected: str
    evidence: list[EvidenceItem] = Field(default_factory=list)
    note: str | None = None


class UnderwritingConsideration(BaseModel):
    category: str
    label: str | None = None
    status: Literal["available", "partial", "missing", "unavailable"]
    summary: str
    appetite_rule_applied: bool = False
    evidence: list[EvidenceItem] = Field(default_factory=list)


class TraceEvent(BaseModel):
    id: str
    submission_id: str | None = None
    tool: str
    purpose: str
    status: TraceStatus
    started_at: datetime
    duration_ms: int
    fields: list[str] = Field(default_factory=list)
    fact_ids: list[str] = Field(default_factory=list)
    adapter: str | None = None
    budget_remaining: int | None = None
    result_summary: str
    error: str | None = None
    records_inspected: int | None = None
    facts_changed: int | None = None
    page_count: int | None = None
    source_resource: str | None = None


class QueryAudit(BaseModel):
    id: str
    payload: dict[str, Any]
    schema_digest: str
    resource: str
    pagination: dict[str, int] = Field(default_factory=dict)
    started_at: datetime
    duration_ms: int
    returned_count: int = 0
    returned_total: int | None = None
    status: Literal["success", "failure"]
    failed_field_path: str | None = None
    error: str | None = None
    attribution: dict[str, Any] = Field(default_factory=dict)


class Assessment(BaseModel):
    submission_id: str
    submission_number: str
    insured_name: str
    received_date: date | None = None
    status: AssessmentStatus
    target_matches: int
    target_preferences_total: int
    evidence_completeness: float
    premium: float | None = None
    tiv: float | None = None
    primary_state: str | None = None
    matched_preferences: list[RuleOutcome] = Field(default_factory=list)
    passed_requirements: list[RuleOutcome] = Field(default_factory=list)
    failed_requirements: list[RuleOutcome] = Field(default_factory=list)
    unresolved_rules: list[RuleOutcome] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    recommended_action: str
    explanation: str
    evidence: list[EvidenceItem] = Field(default_factory=list)
    ledger: EvidenceLedger | None = None
    profile_considerations: list[UnderwritingConsideration] = Field(default_factory=list)
    cope: list[UnderwritingConsideration] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    run_id: str = ""
    guideline_id: str
    guideline_version: str
    guideline_effective_date: date
    explanation_source: Literal["openai", "deterministic"] = "deterministic"


class QueueSubmission(BaseModel):
    submission_id: str
    submission_number: str
    insured_name: str
    received_date: date | None = None
    premium: float | None = None
    tiv: float | None = None
    primary_state: str | None = None
    line_of_business: str | None = None
    scope_status: Literal["in_scope", "outside_scope", "scope_unknown", "not_evaluated"] = "not_evaluated"
    analysis_status: Literal["not_analyzed"] = "not_analyzed"


class BatchAnalysisRequest(BaseModel):
    submission_ids: list[str] | None = None
    force_schema_refresh: bool = False
    guideline_id: str
    guideline_version: str | None = None
    model_provider: ModelProvider = "openai"


class AnalysisRun(BaseModel):
    run_id: str
    mode: Literal["demo", "live"]
    status: Literal["completed", "partial", "failed"]
    created_at: datetime
    schema_source: Literal["demo", "live", "cache"]
    assessments: list[Assessment]
    trace: list[TraceEvent]
    errors: list[str] = Field(default_factory=list)
    guideline_id: str
    guideline_name: str
    guideline_version: str
    guideline_effective_date: date
    profile_id: str | None = None
    available_submissions: int = 0
    in_scope_submissions: int = 0
    outside_scope_submissions: int = 0
    scope_unknown_submissions: int = 0
    assessed_submissions: int = 0
    total_submissions: int = 0
    applicable_submissions: int = 0
    not_applicable_submissions: int = 0
    duration_ms: int = 0
    tool_call_count: int = 0
    unresolved_fact_count: int = 0
    useful_fact_changes: int = 0
    query_count: int = 0
    query_metrics: dict[str, int] = Field(default_factory=dict)
    activity: list[TraceEvent] = Field(default_factory=list)
    agent_mode: AgentMode = "openai_required"
    agent_model: str | None = None
    agent_summary: str | None = None
    agent_adaptations: list[str] = Field(default_factory=list)
    agent_stop_reason: str | None = None
    unresolved_facts_by_reason: dict[str, int] = Field(default_factory=dict)
    model_latency_ms: int = 0
    model_prompt_tokens: int = 0
    model_completion_tokens: int = 0
    model_valid_output_rate: float | None = None
    model_agreement_rate: float | None = None


class SchemaStatus(BaseModel):
    mode: Literal["demo", "live"]
    configured: bool
    cached: bool
    resource_count: int
    source: Literal["demo", "live", "cache", "not_loaded"]


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str = "underwriteiq-api"
    mode: Literal["demo", "live"]
    federato_configured: bool
    openai_configured: bool
    baseten_configured: bool
    baseten_error: str | None = None
