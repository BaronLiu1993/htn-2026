from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


AssessmentStatus = Literal[
    "target", "acceptable", "needs_review", "out_of_appetite"
]
RuleState = Literal["passed", "failed", "matched", "not_matched", "unresolved"]
TraceStatus = Literal["success", "retry", "failure", "cached"]
AgentMode = Literal["openai", "openai_required"]


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
    source_records: dict[str, list[str]] = Field(default_factory=dict)


class EvidenceItem(BaseModel):
    resource: str
    record_id: str
    field: str
    value: Any
    label: str


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
    category: Literal["construction", "occupancy", "protection", "exposure"]
    status: Literal["available", "partial", "missing"]
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
    result_summary: str
    error: str | None = None


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
    cope: list[UnderwritingConsideration] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    run_id: str = ""
    appetite_id: str
    appetite_version: str
    appetite_effective_date: date
    explanation_source: Literal["openai", "deterministic"] = "deterministic"


class QueueSubmission(BaseModel):
    submission_id: str
    submission_number: str
    insured_name: str
    received_date: date | None = None
    premium: float | None = None
    tiv: float | None = None
    primary_state: str | None = None
    analysis_status: Literal["not_analyzed"] = "not_analyzed"


class BatchAnalysisRequest(BaseModel):
    submission_ids: list[str] | None = None
    force_schema_refresh: bool = False


class AnalysisRun(BaseModel):
    run_id: str
    mode: Literal["demo", "live"]
    status: Literal["completed", "partial", "failed"]
    created_at: datetime
    schema_source: Literal["demo", "live", "cache"]
    assessments: list[Assessment]
    trace: list[TraceEvent]
    errors: list[str] = Field(default_factory=list)
    appetite_id: str
    appetite_version: str
    appetite_effective_date: date
    agent_mode: AgentMode = "openai_required"
    agent_model: str | None = None
    agent_summary: str | None = None
    agent_adaptations: list[str] = Field(default_factory=list)


class SchemaStatus(BaseModel):
    mode: Literal["demo", "live"]
    configured: bool
    cached: bool
    resource_count: int
    source: Literal["demo", "live", "cache", "not_loaded"]


class AppetiteStatus(BaseModel):
    id: str
    name: str
    version: str
    effective_from: date
    source: str
    requirement_count: int
    preference_count: int


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str = "underwriteiq-api"
    mode: Literal["demo", "live"]
    federato_configured: bool
    openai_configured: bool
