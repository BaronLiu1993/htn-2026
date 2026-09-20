from __future__ import annotations

import asyncio
import time
from datetime import datetime
from uuid import uuid4

from .adapters import DemoFederatoAdapter, FederatoAdapter
from .agent import UnderwritingAgent
from .baseten_agent import BasetenRunResult, BasetenUnderwriter
from .evidence_search import EvidenceSearch
from .config import Settings
from .evidence_ledger import FactMapper
from .evaluator import evaluate_ledger, rank_assessments
from .federato_client import FederatoClient
from .guideline_registry import (
    GuidelinePackage,
    GuidelineRegistry,
    GuidelineSummary,
)
from .live_data import LiveFederatoLoader
from .models import (
    AnalysisRun,
    BatchAnalysisRequest,
    QueueSubmission,
    SchemaStatus,
    SubmissionEvidence,
    TraceEvent,
)
from .profile_registry import InvestigationProfile, ProfileRegistry
from .rule_engine import scope_status
from .schema_registry import SchemaRegistry
from .tool_gateway import ToolGateway


class UnderwriteService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = FederatoClient(settings)
        self.guidelines = GuidelineRegistry()
        self.profiles = ProfileRegistry()
        self.registry: SchemaRegistry | None = None
        self._schema_source: str = "not_loaded"
        self.runs: dict[str, AnalysisRun] = {}
        self.guideline = self.guidelines.resolve(None)
        self.agent = UnderwritingAgent(settings) if settings.openai_configured else None
        self.baseten = BasetenUnderwriter(settings)

    @property
    def mode(self) -> str:
        return "live" if self.settings.federato_configured else "demo"

    def _gateway(self, package: GuidelinePackage, trace: list[TraceEvent]) -> ToolGateway:
        adapter = (
            FederatoAdapter(self.client)
            if self.mode == "live"
            else DemoFederatoAdapter()
        )
        return ToolGateway([adapter], package.tool_policy, trace)

    async def schema_status(self) -> SchemaStatus:
        return SchemaStatus(
            mode=self.mode,
            configured=self.settings.federato_configured,
            cached=self.registry is not None,
            resource_count=len(self.registry.resources) if self.registry else 0,
            source=self._schema_source if self.mode == "live" else "demo",
        )

    def list_guidelines(self) -> list[GuidelineSummary]:
        return self.guidelines.list()

    def get_guideline(self, guideline_id: str, version: str | None = None) -> GuidelinePackage:
        return self.guidelines.resolve(guideline_id, version)

    async def _live_evidence(
        self,
        gateway: ToolGateway,
        package: GuidelinePackage,
        *,
        force_refresh: bool = False,
    ) -> tuple[list[SubmissionEvidence], str, LiveFederatoLoader, SchemaRegistry]:
        raw_schema = await gateway.schema(
            purpose="Identify available underwriting evidence"
        )
        registry = SchemaRegistry(raw_schema)
        self.registry = registry
        self._schema_source = "live"
        loader = LiveFederatoLoader(
            gateway,
            registry,
            gateway.trace.append,
            semantic_resources=package.source_plan.resources,
        )
        evidence = await loader.load()
        return evidence, "live", loader, registry

    async def _evidence(
        self,
        gateway: ToolGateway,
        package: GuidelinePackage,
        *,
        force_refresh: bool = False,
    ) -> tuple[list[SubmissionEvidence], str, LiveFederatoLoader, SchemaRegistry]:
        if self.mode == "live":
            return await self._live_evidence(gateway, package, force_refresh=force_refresh)
        evidence, _, loader, registry = await self._live_evidence(
            gateway, package, force_refresh=True
        )
        self._schema_source = "demo"
        return evidence, "demo", loader, registry

    async def list_submissions(self) -> list[QueueSubmission]:
        package = self.guidelines.resolve(None)
        trace: list[TraceEvent] = []
        gateway = self._gateway(package, trace)
        evidence, _, _, _ = await self._evidence(gateway, package)
        mapper = FactMapper(package)
        output: list[QueueSubmission] = []
        for item in evidence:
            ledger = mapper.build(item)
            output.append(
                QueueSubmission(
                    submission_id=item.id,
                    submission_number=item.submission_number,
                    insured_name=item.insured_name,
                    received_date=item.received_date,
                    premium=item.premium,
                    tiv=item.tiv,
                    primary_state=item.primary_state,
                    line_of_business=item.line_of_business,
                    scope_status=scope_status(package.scope, ledger),
                )
            )
        return output

    def _profile(self, package: GuidelinePackage) -> InvestigationProfile | None:
        if not package.investigation_profile_id:
            return None
        return self.profiles.resolve(package.investigation_profile_id)

    def _failed_run(
        self,
        run_id: str,
        package: GuidelinePackage,
        trace: list[TraceEvent],
        errors: list[str],
        *,
        schema_source: str,
        started: float,
        provider: str,
    ) -> AnalysisRun:
        run = AnalysisRun(
            run_id=run_id,
            mode=self.mode,
            status="failed",
            created_at=datetime.now(),
            schema_source=schema_source,
            assessments=[],
            trace=trace,
            activity=trace,
            errors=errors,
            guideline_id=package.id,
            guideline_name=package.name,
            guideline_version=package.version,
            guideline_effective_date=package.effective_from,
            profile_id=package.investigation_profile_id,
            duration_ms=max(1, int((time.perf_counter() - started) * 1000)),
            tool_call_count=sum(event.adapter is not None for event in trace),
            agent_mode=f"{provider}_required",
            agent_summary="The run failed. No fallback queue was returned.",
        )
        self.runs[run_id] = run
        return run

    async def analyze(self, request: BatchAnalysisRequest) -> AnalysisRun:
        started = time.perf_counter()
        provider = request.model_provider
        run_id = f"run_{uuid4().hex[:12]}"
        trace: list[TraceEvent] = []
        errors: list[str] = []
        try:
            package = self.guidelines.resolve(
                request.guideline_id, request.guideline_version
            )
            profile = self._profile(package)
            self.guideline = package
        except Exception as exc:
            package = self.guideline
            return self._failed_run(
                run_id,
                package,
                trace,
                [f"Invalid guideline selection: {exc}"],
                schema_source="live" if self.mode == "live" else "demo",
                started=started,
                provider=provider,
            )

        trace.append(
            TraceEvent(
                id=f"trace_{uuid4().hex[:10]}",
                tool="load_guideline",
                purpose="Apply the selected underwriting guideline",
                status="success",
                started_at=datetime.now(),
                duration_ms=1,
                fields=[fact.id for fact in package.required_facts],
                fact_ids=[fact.id for fact in package.required_facts],
                result_summary=(
                    f"Using {package.name}, version {package.version}, with "
                    f"{len(package.requirements)} eligibility requirements and "
                    f"{len(package.preferences)} target preferences."
                ),
            )
        )
        if provider == "openai" and self.agent is None:
            return self._failed_run(
                run_id,
                package,
                trace,
                ["OpenAI is required for analysis. Configure OPENAI_API_KEY and restart the backend."],
                schema_source="live" if self.mode == "live" else "demo",
                started=started,
                provider=provider,
            )
        if provider == "baseten":
            baseten_available, baseten_error = await self.baseten.availability()
            if not baseten_available:
                return self._failed_run(
                    run_id,
                    package,
                    trace,
                    [
                        baseten_error
                        or "The UnderwriteIQ model is unavailable. Configure the Baseten CLI and model ID."
                    ],
                    schema_source="live" if self.mode == "live" else "demo",
                    started=started,
                    provider=provider,
                )

        try:
            gateway = self._gateway(package, trace)
            evidence, schema_source, loader, registry = await self._evidence(
                gateway, package, force_refresh=request.force_schema_refresh
            )
        except Exception as exc:
            return self._failed_run(
                run_id,
                package,
                trace,
                [str(exc)],
                schema_source="live" if self.mode == "live" else "demo",
                started=started,
                provider=provider,
            )

        mapper = FactMapper(package)
        mapper.registry = registry
        search = EvidenceSearch(loader, mapper)
        if request.submission_ids:
            requested_ids = set(request.submission_ids)
            selected = [
                (submission, ledger)
                for submission, ledger in zip(search.submissions, search.ledgers)
                if submission.id in requested_ids
            ]
            search.submissions[:] = [submission for submission, _ in selected]
            search.ledgers[:] = [ledger for _, ledger in selected]
        ledger_pairs = list(zip(search.submissions, search.ledgers))
        total_submissions = len(ledger_pairs)
        scope_states = {
            submission.id: scope_status(package.scope, ledger)
            for submission, ledger in ledger_pairs
        }
        applicable_ids = {
            submission_id
            for submission_id, state in scope_states.items()
            if state == "applicable"
        }
        not_applicable_count = sum(
            state == "not_applicable" for state in scope_states.values()
        )
        scope_unknown_count = sum(
            state == "not_evaluated" for state in scope_states.values()
        )
        selected = [
            (submission, ledger)
            for submission, ledger in ledger_pairs
            if submission.id in applicable_ids
        ]
        search.submissions[:] = [submission for submission, _ in selected]
        search.ledgers[:] = [ledger for _, ledger in selected]
        trace.append(
            TraceEvent(
                id=f"trace_{uuid4().hex[:10]}",
                tool="prepare_queue",
                purpose=f"Select submissions relevant to {package.name}",
                status="success",
                started_at=datetime.now(),
                duration_ms=1,
                fact_ids=[package.scope.fact],
                result_summary=(
                    f"Found {len(applicable_ids)} in-scope submissions from "
                    f"{total_submissions} available; {not_applicable_count} are outside "
                    f"scope and {scope_unknown_count} have unknown scope."
                ),
            )
        )

        if provider == "baseten" and applicable_ids:
            try:
                await loader.load_declared_resources()
                search = EvidenceSearch(loader, mapper)
                selected = [
                    (submission, ledger)
                    for submission, ledger in zip(search.submissions, search.ledgers)
                    if submission.id in applicable_ids
                ]
                search.submissions[:] = [submission for submission, _ in selected]
                search.ledgers[:] = [ledger for _, ledger in selected]
            except Exception as exc:
                return self._failed_run(
                    run_id,
                    package,
                    trace,
                    [f"Federato evidence retrieval failed: {exc}"],
                    schema_source=schema_source,
                    started=started,
                    provider=provider,
                )

        ledger_pairs = list(zip(search.submissions, search.ledgers))
        mapped_facts, unsupported_facts = mapper.apply_schema_plan(
            [ledger for _, ledger in ledger_pairs],
            registry,
        )
        trace.append(
            TraceEvent(
                id=f"trace_{uuid4().hex[:10]}",
                tool="plan_fact_sources",
                purpose="Confirm the available submission data can answer the guideline questions",
                status="success",
                started_at=datetime.now(),
                duration_ms=1,
                fact_ids=[*mapped_facts, *unsupported_facts],
                result_summary=(
                    f"The data can answer {len(mapped_facts)} required questions; "
                    f"{len(unsupported_facts)} question(s) have no available source."
                ),
            )
        )

        agent_result = None
        if provider == "openai" and search.submissions:
            try:
                agent_result = await asyncio.wait_for(
                    self.agent.run(
                        search=search,
                        ledgers=search.ledgers,
                        registry=registry,
                        guideline=package,
                        profile=profile,
                        gateway=gateway,
                        mode=self.mode,
                        trace=trace,
                    ),
                    timeout=self.settings.openai_request_timeout_seconds,
                )
            except Exception as exc:
                message = (
                    f"OpenAI analysis exceeded {self.settings.openai_request_timeout_seconds:g} seconds."
                    if isinstance(exc, TimeoutError)
                    else f"OpenAI analysis failed: {exc}"
                )
                trace.append(
                    TraceEvent(
                        id=f"trace_{uuid4().hex[:10]}",
                        tool="openai_agent",
                        purpose="Gather unresolved evidence before evaluation",
                        status="failure",
                        started_at=datetime.now(),
                        duration_ms=1,
                        result_summary="OpenAI analysis failed; no fallback result was returned",
                        error=message,
                    )
                )
                return self._failed_run(
                    run_id,
                    package,
                    trace,
                    [*errors, message],
                    schema_source=schema_source,
                    started=started,
                    provider=provider,
                )

        failed_source_events = [
            event
            for event in trace
            if event.tool == "federato_query" and event.status == "failure"
        ]
        if failed_source_events:
            messages = [
                event.error or "A required Federato evidence query failed."
                for event in failed_source_events
            ]
            return self._failed_run(
                run_id,
                package,
                trace,
                list(dict.fromkeys(messages)),
                schema_source=schema_source,
                started=started,
                provider=provider,
            )

        applicable = list(zip(search.submissions, search.ledgers))
        resolved_by_agent = search.useful_changes

        assessments = []
        for item, ledger in applicable:
            evaluation_started = time.perf_counter()
            assessment = evaluate_ledger(
                item,
                ledger,
                run_id,
                package=package,
                profile=profile,
            )
            assessments.append(assessment)
            trace.append(
                TraceEvent(
                    id=f"trace_{uuid4().hex[:10]}",
                    submission_id=item.id,
                    tool="evaluate_guideline",
                    purpose=f"Apply deterministic requirements for {item.submission_number}",
                    status="success",
                    started_at=datetime.now(),
                    duration_ms=max(1, int((time.perf_counter() - evaluation_started) * 1000)),
                    fact_ids=[fact.fact_id for fact in ledger.facts],
                    result_summary=f"Classified {item.submission_number} as {assessment.status.replace('_', ' ')}",
                )
            )

        baseten_result: BasetenRunResult | None = None
        if provider == "baseten":
            model_started = datetime.now()
            try:
                baseten_result = await self.baseten.run(
                    search.submissions,
                    search.ledgers,
                    assessments,
                    package,
                )
            except Exception as exc:
                message = f"UnderwriteIQ model analysis failed: {exc}"
                trace.append(
                    TraceEvent(
                        id=f"trace_{uuid4().hex[:10]}",
                        tool="baseten_model",
                        purpose="Compare the specialist model with deterministic appetite results",
                        status="failure",
                        started_at=model_started,
                        duration_ms=1,
                        result_summary="The specialist model did not return a usable result",
                        error=message,
                    )
                )
                return self._failed_run(
                    run_id,
                    package,
                    trace,
                    [*errors, message],
                    schema_source=schema_source,
                    started=started,
                    provider=provider,
                )
            if baseten_result.failures:
                errors.extend(
                    f"UnderwriteIQ prediction failed for {failure}"
                    for failure in baseten_result.failures
                )
            trace.append(
                TraceEvent(
                    id=f"trace_{uuid4().hex[:10]}",
                    tool="baseten_model",
                    purpose="Compare the specialist model with deterministic appetite results",
                    status="retry" if baseten_result.failures else "success",
                    started_at=model_started,
                    duration_ms=baseten_result.latency_ms,
                    result_summary=(
                        f"UnderwriteIQ Qwen returned {len(baseten_result.predictions)} of "
                        f"{len(assessments)} predictions with "
                        f"{baseten_result.agreement_rate:.1%} agreement."
                    ),
                    error=(
                        "Some specialist predictions failed; deterministic assessments "
                        "remain available."
                        if baseten_result.failures
                        else None
                    ),
                )
            )
        unresolved = sum(
            fact.state != "verified"
            for _, ledger in applicable
            for fact in ledger.facts
        )
        run = AnalysisRun(
            run_id=run_id,
            mode=self.mode,
            status="partial" if errors else "completed",
            created_at=datetime.now(),
            schema_source=schema_source,
            assessments=rank_assessments(assessments, package),
            trace=trace,
            errors=errors,
            guideline_id=package.id,
            guideline_name=package.name,
            guideline_version=package.version,
            guideline_effective_date=package.effective_from,
            profile_id=package.investigation_profile_id,
            total_submissions=total_submissions,
            applicable_submissions=len(applicable),
            not_applicable_submissions=not_applicable_count,
            scope_unknown_submissions=scope_unknown_count,
            duration_ms=max(1, int((time.perf_counter() - started) * 1000)),
            tool_call_count=gateway.calls,
            unresolved_fact_count=unresolved,
            useful_fact_changes=resolved_by_agent,
            query_count=sum(event.tool == "federato_query" for event in trace),
            activity=[event for event in trace if event.tool not in {"openai_agent", "plan_fact_sources"} or event.status == "failure"],
            agent_mode=provider,
            agent_model=(
                agent_result.model
                if agent_result is not None
                else baseten_result.model if baseten_result is not None else None
            ),
            agent_summary=(
                f"Reviewed all {len(assessments)} submissions against {package.name}. "
                f"The evidence search updated {resolved_by_agent} underwriting answers."
                if provider == "openai"
                else (
                    f"Classified {len(assessments)} normalized submissions with "
                    f"{baseten_result.agreement_rate:.1%} agreement against the deterministic "
                    "guideline engine. Deterministic hard requirements remain authoritative."
                )
            ),
            agent_adaptations=(
                agent_result.report.adaptations if agent_result is not None else []
            ),
            model_latency_ms=(
                agent_result.model_latency_ms
                if agent_result is not None
                else baseten_result.latency_ms if baseten_result is not None else 0
            ),
            model_prompt_tokens=(
                agent_result.prompt_tokens
                if agent_result is not None
                else baseten_result.prompt_tokens if baseten_result is not None else 0
            ),
            model_completion_tokens=(
                agent_result.completion_tokens
                if agent_result is not None
                else baseten_result.completion_tokens if baseten_result is not None else 0
            ),
            model_valid_output_rate=(
                1.0
                if agent_result is not None
                else (
                    len(baseten_result.predictions) / len(assessments)
                    if baseten_result is not None and assessments
                    else 1.0 if baseten_result is not None else None
                )
            ),
            model_agreement_rate=(
                baseten_result.agreement_rate if baseten_result is not None else None
            ),
        )
        self.runs[run_id] = run
        return run

    def get_run(self, run_id: str) -> AnalysisRun | None:
        return self.runs.get(run_id)
