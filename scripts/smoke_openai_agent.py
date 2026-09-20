"""Verify the demo evidence flow; --fixture replays a documented agent query offline."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.agent import UnderwritingAgent
from backend.app.config import Settings
from backend.app.evaluator import evaluate_ledger
from backend.app.models import BatchAnalysisRequest
from backend.app.service import UnderwriteService


class FixtureTransport:
    """A replay fixture, never used in live acceptance or application runs."""

    def __init__(self) -> None:
        self.calls = 0
        self.before_losses = {}
        self.fixture = json.loads((Path(__file__).resolve().parents[1] / "backend/fixtures/evidence-search.json").read_text())

    async def create(self, payload):
        self.calls += 1
        if self.calls == 1:
            return {"output": [{"type": "function_call", "call_id": "fixture-query", "name": "query_federato", "arguments": json.dumps({"purpose": self.fixture["purpose"], "query_json": json.dumps(self.fixture["query"]), "fact_ids": self.fixture["fact_ids"]})}]}
        if self.calls == 2:
            feedback = json.loads(payload["input"][-1]["output"])
            self.before_losses = {item["submission_id"]: item["facts"] for item in feedback["submissions"]}
            arguments = {"purpose": "Check five-year incurred losses because the guideline requires total losses below $100,000.", "query_json": json.dumps(self.fixture["loss_query"]), "fact_ids": ["five_year_loss_total"]}
            return {"output": [{"type": "function_call", "call_id": "fixture-loss-query", "name": "query_federato", "arguments": json.dumps(arguments)}]}
        return {"output_text": json.dumps({"plan_summary": "Reviewed the queue using related policy, building and loss evidence.", "evidence_strategy": [], "adaptations": [], "limitations": ["Offline replay fixture; no OpenAI request was made."], "explanations": []})}


async def main(fixture: bool = False) -> int:
    settings = replace(Settings.from_env(), federato_client_id=None, federato_client_secret=None)
    service = UnderwriteService(settings)
    if fixture:
        transport = FixtureTransport()
        service.agent = UnderwritingAgent(settings, transport=transport)
    run = await service.analyze(BatchAnalysisRequest(guideline_id="guideline-a", force_schema_refresh=True))
    transitions = []
    for assessment in run.assessments:
        if assessment.ledger and assessment.ledger.fact("five_year_loss_total").state == "verified":
            missing = assessment.ledger.model_copy(deep=True)
            if fixture:
                for fact in transport.before_losses.get(assessment.submission_id, []):
                    missing.fact(fact["fact_id"]).state = fact["state"]
                    missing.fact(fact["fact_id"]).value = fact["value"]
            else:
                missing.fact("five_year_loss_total").state = "missing"
                missing.fact("five_year_loss_total").value = None
            submission = next(item for item in service.loader.normalize(service.loader.records) if item.id == assessment.submission_id)
            before = evaluate_ledger(submission, missing, run.run_id, package=service.guideline)
            if before.status != assessment.status:
                transitions.append({"submission_id": assessment.submission_id, "before": before.status, "after": assessment.status})
    report = {
        "verification": "offline replay" if fixture else "OpenAI demo",
        "status": run.status,
        "available_submissions": run.available_submissions,
        "in_scope_submissions": run.in_scope_submissions,
        "outside_scope_submissions": run.outside_scope_submissions,
        "scope_unknown_submissions": run.scope_unknown_submissions,
        "assessment_count": len(run.assessments),
        "query_count": run.query_count,
        "query_metrics": run.query_metrics,
        "duration_ms": run.duration_ms,
        "status_counts": dict(Counter(item.status for item in run.assessments)),
        "unresolved_facts": run.unresolved_fact_count,
        "unresolved_facts_by_reason": run.unresolved_facts_by_reason,
        "agent_stop_reason": run.agent_stop_reason,
        "useful_fact_changes": run.useful_fact_changes,
        "failed_trace_events": sum(event.status == "failure" for event in run.trace),
        "loss_evidence_outcome_changes": transitions,
        "errors": run.errors,
    }
    print(json.dumps(report, indent=2))
    return 0 if run.status == "completed" and len(run.assessments) == 12 and run.useful_fact_changes > 0 and transitions else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", action="store_true")
    raise SystemExit(asyncio.run(main(parser.parse_args().fixture)))
