from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any
from uuid import uuid4

from .adapters import DemoFederatoAdapter, FederatoAdapter
from .agent import UnderwritingAgent
from .explanations import explain_assessments
from .enrichment import enrich_disasters
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
from .live_data import LiveFederatoLoader, ScopeSelection
from .models import (
    AnalysisRun,
    BatchAnalysisRequest,
    EvidenceLedger,
    QueueSubmission,
    SchemaStatus,
    SubmissionEvidence,
    TraceEvent,
)
from .profile_registry import InvestigationProfile, ProfileRegistry
from .schema_registry import SchemaRegistry
from .tool_gateway import ToolGateway


def _query_metrics(gateway: ToolGateway) -> dict[str, int]:
    keys = (
        "records_found",
        "records_repeated",
        "records_missing_identifier",
        "unowned_rows",
        "unrelated_rows",
        "state_changes",
        "value_changes",
        "useful_fact_changes",
        "affected_submissions",
    )
    return {
        key: sum(
            int(audit.attribution.get(key, 0))
            for audit in gateway.query_audits
            if isinstance(audit.attribution.get(key, 0), (int, float))
        )
        for key in keys
    }


def _same_query_group(left: TraceEvent, right: TraceEvent) -> bool:
    return (
        left.tool == right.tool == "federato_query"
        and left.status == right.status == "success"
        and left.source_resource == right.source_resource
        and left.fact_ids == right.fact_ids
    )


def _merge_query_pages(pages: list[TraceEvent]) -> TraceEvent:
    first = pages[0]
    records = sum(event.records_inspected or 0 for event in pages)
    changes = sum(event.facts_changed or 0 for event in pages)
    duration = sum(event.duration_ms for event in pages)
    summary = next(
        (event.result_summary for event in reversed(pages) if event.result_summary),
        first.result_summary,
    )
    return first.model_copy(
        update={
            "duration_ms": max(1, duration),
            "records_inspected": records or None,
            "facts_changed": changes,
            "page_count": len(pages),
            "result_summary": summary,
        }
    )


def _business_activity(trace: list[TraceEvent]) -> list[TraceEvent]:
    selected = [
        event
        for event in trace
        if event.tool
        in {"load_guideline", "prepare_queue", "bind_facts", "federato_query", "plan_fact_sources", "explain_assessments", "external_enrichment"}
        or event.status == "failure"
    ]
    grouped: list[TraceEvent] = []
    buffer: list[TraceEvent] = []

    def flush() -> None:
        if not buffer:
            return
        grouped.append(buffer[0] if len(buffer) == 1 else _merge_query_pages(buffer))
        buffer.clear()

    for event in selected:
        if event.tool == "federato_query" and event.status == "success":
            if buffer and not _same_query_group(buffer[0], event):
                flush()
            buffer.append(event)
            continue
        flush()
        grouped.append(event)
    flush()
    return grouped


class UnderwriteService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = FederatoClient(settings)
        self.guidelines = GuidelineRegistry()
        self.profiles = ProfileRegistry()
        self.registry: SchemaRegistry | None = None
        self._schema_source: str = "not_loaded"
        self.runs: dict[str, AnalysisRun] = {}
        self._run_artifacts: dict[str, dict[str, Any]] = {}
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

    async def _scope_population(
        self,
        gateway: ToolGateway,
        package: GuidelinePackage,
        *,
        submission_ids: list[str] | None = None,
        force_refresh: bool = False,
    ) -> tuple[LiveFederatoLoader, ScopeSelection, str]:
        raw_schema = await gateway.schema(
            purpose="Find the available sources of underwriting evidence."
        )
        self.registry = SchemaRegistry(raw_schema)
        self._schema_source = "live"
        loader = LiveFederatoLoader(
            gateway,
            self.registry,
            gateway.trace.append,
            semantic_resources=package.source_plan.resources,
        )
        self.loader = loader
        selection = await loader.load_scope(
            package,
            requested_ids=submission_ids,
        )
        schema_source = "live" if self.mode == "live" else "demo"
        self._schema_source = schema_source
        return loader, selection, schema_source

    async def list_submissions(self) -> list[QueueSubmission]:
        package = self.guidelines.resolve(None)
        trace: list[TraceEvent] = []
        gateway = self._gateway(package, trace)
        loader, selection, _ = await self._scope_population(gateway, package)
        if selection.duplicate_ids:
            raise RuntimeError(
                "Scope selection returned duplicate submission identifiers."
            )
        await loader.load_insured_records(selection.resource, selection.records)
        all_records = {**loader.records, selection.resource: selection.records}
        evidence = loader.normalize(all_records)
        in_scope = set(selection.in_scope_ids)
        outside_scope = set(selection.outside_scope_ids)
        output: list[QueueSubmission] = []
        for item in evidence:
            scope_status = (
                "in_scope"
                if item.id in in_scope
                else "outside_scope"
                if item.id in outside_scope
                else "scope_unknown"
            )
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
                    scope_status=scope_status,
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
        gateway: ToolGateway | None = None,
        selection: ScopeSelection | None = None,
        ledgers: list[EvidenceLedger] | None = None,
        provider: str = "openai",
    ) -> AnalysisRun:
        unresolved_by_reason: dict[str, int] = {}
        for ledger in ledgers or []:
            for fact in ledger.facts:
                if fact.state != "verified":
                    unresolved_by_reason[fact.state] = (
                        unresolved_by_reason.get(fact.state, 0) + 1
                    )
        required_source_failed = any(
            event.tool == "federato_query" and event.status == "failure"
            for event in trace
        )
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
            available_submissions=selection.available_count if selection else 0,
            in_scope_submissions=len(selection.in_scope_ids) if selection else 0,
            outside_scope_submissions=len(selection.outside_scope_ids) if selection else 0,
            scope_unknown_submissions=len(selection.unknown_scope_ids) if selection else 0,
            assessed_submissions=0,
            total_submissions=selection.available_count if selection else 0,
            applicable_submissions=len(selection.in_scope_ids) if selection else 0,
            not_applicable_submissions=len(selection.outside_scope_ids) if selection else 0,
            duration_ms=max(1, int((time.perf_counter() - started) * 1000)),
            tool_call_count=sum(event.adapter is not None for event in trace),
            query_count=len(gateway.query_audits) if gateway else 0,
            query_metrics=_query_metrics(gateway) if gateway else {},
            agent_mode=f"{provider}_required",
            agent_summary="The run failed. No fallback queue was returned.",
            agent_stop_reason=(
                "required_source_failure"
                if required_source_failed
                else "run_failed"
            ),
            unresolved_facts_by_reason=unresolved_by_reason,
        )
        self.runs[run_id] = run
        self._store_artifact(run, gateway=gateway, ledgers=ledgers or [])
        return run

    def _store_artifact(
        self,
        run: AnalysisRun,
        *,
        gateway: ToolGateway | None,
        ledgers: list[EvidenceLedger],
    ) -> None:
        self._run_artifacts[run.run_id] = {
            "schema_digest": (
                gateway.registry.schema_digest()
                if gateway is not None and gateway.registry is not None
                else None
            ),
            "query_audits": [
                audit.model_dump(mode="json")
                for audit in (gateway.query_audits if gateway is not None else [])
            ],
            "trace": [event.model_dump(mode="json") for event in run.trace],
            "ledgers": [ledger.model_dump(mode="json") for ledger in ledgers],
            "counts": {
                "available": run.available_submissions,
                "in_scope": run.in_scope_submissions,
                "outside_scope": run.outside_scope_submissions,
                "scope_unknown": run.scope_unknown_submissions,
                "assessed": run.assessed_submissions,
            },
            "agent_stop_reason": run.agent_stop_reason,
            "unresolved_facts_by_reason": run.unresolved_facts_by_reason,
        }

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
                purpose="Apply the selected underwriting guideline.",
                status="success",
                started_at=datetime.now(),
                duration_ms=1,
                fields=[fact.id for fact in package.required_facts],
                fact_ids=[fact.id for fact in package.required_facts],
                result_summary=(
                    f"The system uses {package.name}. Version {package.version}."
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
            loader, selection, schema_source = await self._scope_population(
                gateway,
                package,
                submission_ids=request.submission_ids,
                force_refresh=request.force_schema_refresh,
            )
            if selection.duplicate_ids:
                raise RuntimeError(
                    "Scope selection returned duplicate submission identifiers: "
                    + ", ".join(sorted(set(selection.duplicate_ids))[:10])
                )
        except Exception as exc:
            failed_selection = locals().get("selection")
            if failed_selection is None:
                failed_loader = getattr(self, "loader", None)
                failed_selection = getattr(failed_loader, "scope_selection", None)
            return self._failed_run(
                run_id,
                package,
                trace,
                [str(exc)],
                schema_source="live" if self.mode == "live" else "demo",
                started=started,
                gateway=locals().get("gateway"),
                selection=failed_selection,
                provider=provider,
            )

        mapper = FactMapper(package)
        mapper.registry = self.registry
        search = EvidenceSearch(self.loader, mapper)
        ledger_pairs = list(zip(search.submissions, search.ledgers))
        mapped_facts, unsupported_facts = mapper.apply_bindings(
            [ledger for _, ledger in ledger_pairs],
            self.registry or gateway.registry,
        )
        search.ledgers[:] = [mapper.build(item) for item in search.submissions]
        ledger_pairs = list(zip(search.submissions, search.ledgers))
        trace.append(
            TraceEvent(
                id=f"trace_{uuid4().hex[:10]}",
                tool="plan_fact_sources",
                purpose="Link each guideline question to a source field.",
                status="success",
                started_at=datetime.now(),
                duration_ms=1,
                fact_ids=[*mapped_facts, *unsupported_facts],
                result_summary=(
                    f"The system linked {len(mapped_facts)} guideline questions to source fields. "
                    f"{len(unsupported_facts)} questions have no source field."
                    if unsupported_facts
                    else f"The system linked all {len(mapped_facts)} guideline questions to source fields."
                ),
            )
        )
        applicable = ledger_pairs
        trace.append(
            TraceEvent(
                id=f"trace_{uuid4().hex[:10]}",
                tool="prepare_queue",
                purpose="Find submissions that match this guideline.",
                status="success",
                started_at=datetime.now(),
                duration_ms=1,
                fact_ids=[package.scope.fact],
                result_summary=(
                    f"{len(selection.in_scope_ids)} submissions are in scope. "
                    f"{len(selection.outside_scope_ids)} submissions are outside scope."
                ),
            )
        )

        agent_result = None
        if provider == "openai":
            if self.agent is None:
                return self._failed_run(
                    run_id,
                    package,
                    trace,
                    ["OpenAI is required for analysis. Configure OPENAI_API_KEY and restart the backend."],
                    schema_source=schema_source,
                    started=started,
                    gateway=gateway,
                    selection=selection,
                    provider=provider,
                )
            try:
                agent_result = await asyncio.wait_for(
                    self.agent.run(
                        search=search,
                        ledgers=search.ledgers,
                        registry=self.registry or gateway.registry,
                        guideline=package,
                        profile=profile,
                        gateway=gateway,
                        mode=self.mode,
                        trace=trace,
                    ),
                    timeout=self.settings.openai_request_timeout_seconds,
                )
            except Exception as exc:
                source_failed = any(
                    event.tool == "federato_query" and event.status == "failure"
                    for event in trace
                )
                if source_failed:
                    message = f"Required Federato evidence query failed: {exc}"
                elif isinstance(exc, TimeoutError):
                    message = (
                        "OpenAI analysis exceeded "
                        f"{self.settings.openai_request_timeout_seconds:g} seconds."
                    )
                else:
                    message = f"OpenAI analysis failed: {exc}"
                if not source_failed:
                    trace.append(
                        TraceEvent(
                            id=f"trace_{uuid4().hex[:10]}",
                            tool="openai_agent",
                            purpose="Need the evidence for this guideline.",
                            status="failure",
                            started_at=datetime.now(),
                            duration_ms=1,
                            result_summary="The evidence search did not finish.",
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
                    gateway=gateway,
                    selection=selection,
                    ledgers=search.ledgers,
                    provider=provider,
                )
        elif provider == "baseten":
            try:
                keep_ids = {item.id for item in search.submissions}
                await loader.load_declared_resources()
                search = EvidenceSearch(loader, mapper)
                selected = [
                    (submission, ledger)
                    for submission, ledger in zip(search.submissions, search.ledgers)
                    if submission.id in keep_ids
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
                    gateway=gateway,
                    selection=selection,
                    provider=provider,
                )

        mapper.finalize_unbound(search.ledgers)
        try:
            if provider == "openai" and gateway.budget_remaining > 0:
                await search.fill_policies(gateway)
                await search.fill_headquarters(gateway)
        except Exception as exc:
            return self._failed_run(
                run_id, package, trace, [*errors, f"Required evidence search failed: {exc}"],
                schema_source=schema_source, started=started, gateway=gateway,
                selection=selection, ledgers=search.ledgers, provider=provider,
            )
        mapper.finalize_unbound(search.ledgers)
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
                    purpose="Apply the guideline rules to this submission.",
                    status="success",
                    started_at=datetime.now(),
                    duration_ms=max(1, int((time.perf_counter() - evaluation_started) * 1000)),
                    fact_ids=[fact.fact_id for fact in ledger.facts],
                    result_summary=(
                        f"The submission is {assessment.status.replace('_', ' ')}."
                    ),
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
                    gateway=gateway,
                    selection=selection,
                    ledgers=search.ledgers,
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

        if self.mode == "live":
            before_enrichment = {item.submission_id: index for index, item in enumerate(rank_assessments(assessments, package))}
            await enrich_disasters(assessments, trace)
            moved = 0
            for index, item in enumerate(rank_assessments(assessments, package)):
                item.enrichment_rank_change = before_enrichment[item.submission_id] - index
                moved += item.enrichment_rank_change != 0
            if trace[-1].tool == "external_enrichment" and trace[-1].status == "success":
                trace[-1].result_summary += f" {moved} submissions changed position within their appetite status."
        if provider == "openai" and self.agent is not None:
            await explain_assessments(self.agent, assessments, trace)

        if agent_result is not None and agent_result.stop_reason == "no_useful_search" and search.policy_search_completed:
            if search.coverage()["submissions_without_policy_count"]:
                agent_result.stop_reason = "source_data_missing"
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
            available_submissions=selection.available_count,
            in_scope_submissions=len(selection.in_scope_ids),
            outside_scope_submissions=len(selection.outside_scope_ids),
            scope_unknown_submissions=len(selection.unknown_scope_ids),
            assessed_submissions=len(assessments),
            total_submissions=selection.available_count,
            applicable_submissions=len(selection.in_scope_ids),
            not_applicable_submissions=len(selection.outside_scope_ids),
            duration_ms=max(1, int((time.perf_counter() - started) * 1000)),
            tool_call_count=gateway.calls,
            unresolved_fact_count=unresolved,
            useful_fact_changes=resolved_by_agent,
            query_count=len(gateway.query_audits),
            query_metrics=_query_metrics(gateway),
            activity=_business_activity(trace),
            agent_mode=provider,
            agent_model=(
                agent_result.model
                if agent_result is not None
                else baseten_result.model if baseten_result is not None else None
            ),
            agent_summary=(
                agent_result.report.plan_summary
                if agent_result is not None
                else (
                    f"Classified {len(assessments)} normalized submissions with "
                    f"{baseten_result.agreement_rate:.1%} agreement against the deterministic "
                    "guideline engine. Deterministic hard requirements remain authoritative."
                    if baseten_result is not None
                    else None
                )
            ),
            agent_adaptations=(
                agent_result.report.adaptations if agent_result is not None else []
            ),
            agent_stop_reason=(
                agent_result.stop_reason if agent_result is not None else None
            ),
            unresolved_facts_by_reason=search.coverage()["unresolved_by_reason"],
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
        self._store_artifact(run, gateway=gateway, ledgers=search.ledgers)
        return run

    def get_run(self, run_id: str) -> AnalysisRun | None:
        return self.runs.get(run_id)

    def get_run_artifact(self, run_id: str) -> dict[str, Any] | None:
        return self._run_artifacts.get(run_id)
