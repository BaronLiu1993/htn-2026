from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .app.config import settings
from .app.guideline_registry import GuidelinePackage, GuidelineSummary
from .app.models import (
    AnalysisRun,
    BatchAnalysisRequest,
    HealthResponse,
    QueueSubmission,
    SchemaStatus,
    TraceEvent,
)
from .app.profile_registry import InvestigationProfile
from .app.service import UnderwriteService


app = FastAPI(
    title="UnderwriteIQ API",
    version="0.1.0",
    description="Guideline-agnostic Federato underwriting triage.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

service = UnderwriteService(settings)


@app.get("/", include_in_schema=False)
async def root() -> dict[str, str]:
    return {"service": "UnderwriteIQ API", "docs": "/docs"}


@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        mode=service.mode,
        federato_configured=settings.federato_configured,
        openai_configured=settings.openai_configured,
        baseten_configured=bool(
            settings.baseten_model_id and Path(settings.baseten_cli_path).is_file()
        ),
    )


@app.get("/api/schema/status", response_model=SchemaStatus)
async def schema_status() -> SchemaStatus:
    return await service.schema_status()


@app.get("/api/guidelines", response_model=list[GuidelineSummary])
async def guidelines() -> list[GuidelineSummary]:
    return service.list_guidelines()


@app.get("/api/guidelines/{guideline_id}", response_model=GuidelinePackage)
async def guideline(
    guideline_id: str, version: str | None = None
) -> GuidelinePackage:
    try:
        return service.get_guideline(guideline_id, version)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/profiles", response_model=list[InvestigationProfile])
async def profiles() -> list[InvestigationProfile]:
    return service.profiles.list()


@app.get("/api/submissions", response_model=list[QueueSubmission])
async def submissions() -> list[QueueSubmission]:
    try:
        return await service.list_submissions()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/analysis/batch", response_model=AnalysisRun)
async def analyze_batch(request: BatchAnalysisRequest) -> AnalysisRun:
    run = await service.analyze(request)
    if run.status == "failed":
        raise HTTPException(
            status_code=502,
            detail={
                "run_id": run.run_id,
                "errors": run.errors,
                "trace": [event.model_dump(mode="json") for event in run.trace],
            },
        )
    return run


@app.get("/api/runs/{run_id}", response_model=AnalysisRun)
async def get_run(run_id: str) -> AnalysisRun:
    run = service.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@app.get("/api/runs/{run_id}/trace", response_model=list[TraceEvent])
async def get_trace(run_id: str) -> list[TraceEvent]:
    run = service.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run.trace
