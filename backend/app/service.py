from __future__ import annotations

import time
from datetime import datetime
from uuid import uuid4

from .agent import UnderwritingAgent, apply_agent_report
from .appetite_loader import DEFAULT_APPETITE, load_appetite
from .config import Settings
from .demo_data import DEMO_SUBMISSIONS
from .demo_federato import DEMO_SCHEMA
from .evaluator import evaluate_submission, rank_assessments
from .federato_client import FederatoClient
from .live_data import LiveFederatoLoader
from .models import (
    AnalysisRun,
    AppetiteStatus,
    BatchAnalysisRequest,
    QueueSubmission,
    SchemaStatus,
    SubmissionEvidence,
    TraceEvent,
)
from .schema_registry import SchemaRegistry


class UnderwriteService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = FederatoClient(settings)
        self.registry: SchemaRegistry | None = None
        self._schema_source: str = "not_loaded"
        self._evidence_cache: list[SubmissionEvidence] | None = None
        self.runs: dict[str, AnalysisRun] = {}
        self.appetite = DEFAULT_APPETITE
        self.agent = UnderwritingAgent(settings) if settings.openai_configured else None

    @property
    def mode(self) -> str:
        return "live" if self.settings.federato_configured else "demo"

    async def schema_status(self) -> SchemaStatus:
        return SchemaStatus(
            mode=self.mode,
            configured=self.settings.federato_configured,
            cached=self.registry is not None,
            resource_count=len(self.registry.resources) if self.registry else (7 if self.mode == "demo" else 0),
            source=self._schema_source if self.mode == "live" else "demo",
        )

    def appetite_status(self) -> AppetiteStatus:
        appetite = load_appetite()
        self.appetite = appetite
        return AppetiteStatus(
            id=appetite.id,
            name=appetite.name,
            version=appetite.version,
            effective_from=appetite.effective_from,
            source=appetite.source,
            requirement_count=len(appetite.requirements),
            preference_count=len(appetite.preferences),
        )

    async def _live_evidence(
        self, trace: list[TraceEvent], *, force_refresh: bool = False
    ) -> tuple[list[SubmissionEvidence], str]:
        if self._evidence_cache is not None and not force_refresh:
            trace.append(
                TraceEvent(
                    id=f"trace_{uuid4().hex[:10]}",
                    tool="discover_schema",
                    purpose="Validate the evidence plan against Federato's schema",
                    status="cached",
                    started_at=datetime.now(),
                    duration_ms=1,
                    result_summary=f"Used cached schema with {len(self.registry.resources) if self.registry else 0} resources",
                )
            )
            return self._evidence_cache, "cache"

        started = time.perf_counter()
        raw_schema = await self.client.schema()
        self.registry = SchemaRegistry(raw_schema)
        self._schema_source = "live"
        trace.append(
            TraceEvent(
                id=f"trace_{uuid4().hex[:10]}",
                tool="discover_schema",
                purpose="Discover valid Federato resources, fields, and relationships",
                status="success",
                started_at=datetime.now(),
                duration_ms=max(1, int((time.perf_counter() - started) * 1000)),
                result_summary=f"Discovered {len(self.registry.resources)} resources",
            )
        )
        loader = LiveFederatoLoader(self.client, self.registry, trace.append)
        self._evidence_cache = await loader.load()
        return self._evidence_cache, "live"

    async def _evidence(
        self, trace: list[TraceEvent], *, force_refresh: bool = False
    ) -> tuple[list[SubmissionEvidence], str]:
        if self.mode == "live":
            return await self._live_evidence(trace, force_refresh=force_refresh)
        trace.extend(
            [
                TraceEvent(
                    id=f"trace_{uuid4().hex[:10]}",
                    tool="discover_schema",
                    purpose="Discover valid Federato resources, fields, and relationships",
                    status="success",
                    started_at=datetime.now(),
                    duration_ms=18,
                    result_summary="Loaded demo schema fixture with 7 resources",
                ),
                TraceEvent(
                    id=f"trace_{uuid4().hex[:10]}",
                    tool="query_federato",
                    purpose="Retrieve submissions and linked policy evidence",
                    status="success",
                    started_at=datetime.now(),
                    duration_ms=42,
                    fields=[
                        "submission_type",
                        "line_of_business",
                        "premium",
                        "tiv",
                        "state",
                    ],
                    result_summary=f"Retrieved {len(DEMO_SUBMISSIONS)} demo submissions",
                ),
                TraceEvent(
                    id=f"trace_{uuid4().hex[:10]}",
                    tool="query_federato",
                    purpose="Retrieve linked buildings and five-year claims",
                    status="success",
                    started_at=datetime.now(),
                    duration_ms=37,
                    fields=["year_built", "construction_type", "loss_date", "loss_value"],
                    result_summary="Resolved building and claim evidence through schema-declared references",
                ),
            ]
        )
        return DEMO_SUBMISSIONS, "demo"

    async def list_submissions(self) -> list[QueueSubmission]:
        trace: list[TraceEvent] = []
        evidence, _ = await self._evidence(trace)
        return [
            QueueSubmission(
                submission_id=item.id,
                submission_number=item.submission_number,
                insured_name=item.insured_name,
                received_date=item.received_date,
                premium=item.premium,
                tiv=item.tiv,
                primary_state=item.primary_state,
            )
            for item in evidence
        ]

    async def analyze(self, request: BatchAnalysisRequest) -> AnalysisRun:
        run_id = f"run_{uuid4().hex[:12]}"
        trace: list[TraceEvent] = []
        errors: list[str] = []
        appetite_started = time.perf_counter()
        try:
            # Reload for every run so an approved rule-pack update takes effect
            # without code changes or a server restart.
            self.appetite = load_appetite()
            trace.append(
                TraceEvent(
                    id=f"trace_{uuid4().hex[:10]}",
                    tool="load_appetite",
                    purpose="Load and validate the active carrier appetite",
                    status="success",
                    started_at=datetime.now(),
                    duration_ms=max(1, int((time.perf_counter() - appetite_started) * 1000)),
                    fields=[rule.fact for rule in self.appetite.requirements + self.appetite.preferences],
                    result_summary=(
                        f"Loaded {self.appetite.id} version {self.appetite.version} "
                        f"with {len(self.appetite.requirements)} requirements and "
                        f"{len(self.appetite.preferences)} target preferences"
                    ),
                )
            )
        except Exception as exc:
            run = AnalysisRun(
                run_id=run_id,
                mode=self.mode,
                status="failed",
                created_at=datetime.now(),
                schema_source="live" if self.mode == "live" else "demo",
                assessments=[],
                trace=trace,
                errors=[f"Invalid appetite configuration: {exc}"],
                appetite_id=self.appetite.id,
                appetite_version=self.appetite.version,
                appetite_effective_date=self.appetite.effective_from,
            )
            self.runs[run_id] = run
            return run
        try:
            evidence, schema_source = await self._evidence(
                trace, force_refresh=request.force_schema_refresh
            )
        except Exception as exc:
            run = AnalysisRun(
                run_id=run_id,
                mode=self.mode,
                status="failed",
                created_at=datetime.now(),
                schema_source="live" if self.mode == "live" else "demo",
                assessments=[],
                trace=trace,
                errors=[str(exc)],
                appetite_id=self.appetite.id,
                appetite_version=self.appetite.version,
                appetite_effective_date=self.appetite.effective_from,
            )
            self.runs[run_id] = run
            return run

        wanted = set(request.submission_ids or [])
        selected = [item for item in evidence if not wanted or item.id in wanted]
        missing_ids = sorted(wanted - {item.id for item in selected})
        if missing_ids:
            errors.append(f"Unknown submission IDs: {', '.join(missing_ids)}")

        assessments = []
        for item in selected:
            started = time.perf_counter()
            assessment = evaluate_submission(item, run_id, appetite=self.appetite)
            assessments.append(assessment)
            trace.append(
                TraceEvent(
                    id=f"trace_{uuid4().hex[:10]}",
                    submission_id=item.id,
                    tool="evaluate_appetite",
                    purpose=f"Apply hard requirements before preferences for {item.submission_number}",
                    status="success",
                    started_at=datetime.now(),
                    duration_ms=max(1, int((time.perf_counter() - started) * 1000)),
                    fields=[
                        "submission_type",
                        "line_of_business",
                        "primary_state",
                        "tiv",
                        "premium",
                        "buildings",
                        "claims",
                    ],
                    result_summary=(
                        f"Classified {item.submission_number} as {assessment.status} "
                        f"with {assessment.target_matches} of "
                        f"{assessment.target_preferences_total} target preferences"
                    ),
                )
            )

        agent_mode = "deterministic_fallback"
        agent_model: str | None = None
        agent_summary = "Deterministic analysis completed; OpenAI agent is not configured."
        agent_adaptations: list[str] = []
        if self.agent is not None:
            agent_registry = self.registry or SchemaRegistry(DEMO_SCHEMA)
            try:
                agent_result = await self.agent.run(
                    assessments=assessments,
                    registry=agent_registry,
                    appetite=self.appetite,
                    federato=self.client,
                    mode=self.mode,
                    trace=trace,
                )
                applied, report_warnings = apply_agent_report(
                    assessments, agent_result.report
                )
                for warning in report_warnings:
                    trace.append(
                        TraceEvent(
                            id=f"trace_{uuid4().hex[:10]}",
                            tool="validate_agent_report",
                            purpose="Ground AI explanations in verified evidence",
                            status="failure",
                            started_at=datetime.now(),
                            duration_ms=1,
                            result_summary="Rejected an unsupported AI explanation detail",
                            error=warning,
                        )
                    )
                agent_mode = "openai"
                agent_model = agent_result.model
                agent_summary = (
                    f"{agent_result.report.plan_summary} Applied {applied} grounded "
                    f"explanation(s) after {agent_result.tool_calls} tool call(s)."
                )
                agent_adaptations = agent_result.report.adaptations
            except Exception as exc:
                agent_summary = (
                    "OpenAI agent failed safely; deterministic classifications and explanations "
                    "were preserved."
                )
                trace.append(
                    TraceEvent(
                        id=f"trace_{uuid4().hex[:10]}",
                        tool="openai_agent",
                        purpose="Plan and verify underwriting evidence",
                        status="failure",
                        started_at=datetime.now(),
                        duration_ms=1,
                        result_summary="Fell back to deterministic analysis",
                        error=str(exc),
                    )
                )

        run = AnalysisRun(
            run_id=run_id,
            mode=self.mode,
            status="partial" if errors else "completed",
            created_at=datetime.now(),
            schema_source=schema_source,
            assessments=rank_assessments(assessments),
            trace=trace,
            errors=errors,
            appetite_id=self.appetite.id,
            appetite_version=self.appetite.version,
            appetite_effective_date=self.appetite.effective_from,
            agent_mode=agent_mode,
            agent_model=agent_model,
            agent_summary=agent_summary,
            agent_adaptations=agent_adaptations,
        )
        self.runs[run_id] = run
        return run

    def get_run(self, run_id: str) -> AnalysisRun | None:
        return self.runs.get(run_id)
