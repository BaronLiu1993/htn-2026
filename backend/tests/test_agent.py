import json
from dataclasses import replace
from datetime import date

import pytest

from backend.app.agent import AgentReport, UnderwritingAgent, apply_agent_report
from backend.app.appetite_loader import DEFAULT_APPETITE
from backend.app.config import settings
from backend.app.demo_data import DEMO_SUBMISSIONS
from backend.app.demo_federato import DEMO_SCHEMA
from backend.app.evaluator import evaluate_submission
from backend.app.federato_client import FederatoClient
from backend.app.models import BatchAnalysisRequest
from backend.app.schema_registry import SchemaRegistry
from backend.app.service import UnderwriteService


class ScriptedTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    async def create(self, payload):
        self.requests.append(payload)
        return self.responses.pop(0)


class BrokenAgent:
    async def run(self, **kwargs):
        raise RuntimeError("simulated provider outage")


def _call(name, call_id, arguments=None):
    return {
        "model": "test-model",
        "output": [
            {
                "type": "function_call",
                "id": f"fc_{call_id}",
                "status": "completed",
                "name": name,
                "call_id": call_id,
                "arguments": json.dumps(arguments or {}),
            }
        ],
    }


def _report(
    submission_id="101",
    evidence_ids=None,
    *,
    contradictions=None,
    missing_information=None,
):
    payload = {
        "plan_summary": "Inspected the schema and appetite, then queried policy evidence.",
        "evidence_strategy": ["Start with policy fields", "Deepen only when evidence is missing"],
        "adaptations": ["Corrected an invalid field before retrying"],
        "limitations": ["External enrichment was not requested"],
        "explanations": [
            {
                "submission_id": submission_id,
                "explanation": "This account passes every hard requirement and matches all four target preferences. The verified policy and building evidence support prioritizing it for underwriting review.",
                "key_factors": ["Eligible state", "Target TIV", "Target premium"],
                "contradictions": contradictions or [],
                "missing_information": missing_information or [],
                "evidence_ids": evidence_ids or [submission_id],
            }
        ],
    }
    return {
        "model": "test-model",
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": json.dumps(payload)}],
            }
        ],
    }


@pytest.mark.anyio
async def test_agent_uses_required_tools_repairs_query_and_returns_report():
    invalid_query = {
        "purpose": "Inspect target policy evidence",
        "query_json": json.dumps(
            {"resource": "Policy", "where": {"made_up_field": "x"}}
        ),
    }
    valid_query = {
        "purpose": "Inspect policy appetite fields",
        "query_json": json.dumps(
            {
                "resource": "Policy",
                "select": ["id", "line_of_business", "premium", "tiv", "primary_state"],
                "pagination": {"limit": 10, "offset": 0},
            }
        ),
    }
    transport = ScriptedTransport(
        [
            _call("inspect_schema", "schema-1"),
            _call("get_appetite", "appetite-1"),
            _call("query_federato", "query-1", invalid_query),
            _call("query_federato", "query-2", valid_query),
            _report(),
        ]
    )
    configured = replace(
        settings,
        openai_api_key="test-key",
        openai_model="test-model",
        openai_max_turns=6,
        openai_max_query_calls=4,
    )
    agent = UnderwritingAgent(configured, transport=transport)
    assessment = evaluate_submission(
        DEMO_SUBMISSIONS[0], "run_agent", as_of=date(2026, 9, 19)
    )
    trace = []

    result = await agent.run(
        assessments=[assessment],
        registry=SchemaRegistry(DEMO_SCHEMA),
        appetite=DEFAULT_APPETITE,
        federato=FederatoClient(configured),
        mode="demo",
        trace=trace,
    )

    assert result.model == "test-model"
    assert result.tool_calls == 4
    assert result.report.explanations[0].submission_id == "101"
    query_events = [event for event in trace if event.tool == "agent_query_federato"]
    assert [event.status for event in query_events] == ["failure", "success"]
    assert all(request["store"] is False for request in transport.requests)
    assert all(tool["strict"] is True for tool in transport.requests[0]["tools"])
    replayed_call = transport.requests[1]["input"][1]
    assert replayed_call == {
        "type": "function_call",
        "call_id": "schema-1",
        "name": "inspect_schema",
        "arguments": "{}",
    }


def test_agent_report_only_replaces_grounded_known_explanations():
    assessment = evaluate_submission(
        DEMO_SUBMISSIONS[0], "run_agent", as_of=date(2026, 9, 19)
    )
    report_text = _report(evidence_ids=["not-real"])["output"][0]["content"][0]["text"]
    applied, warnings = apply_agent_report(
        [assessment], AgentReport.model_validate_json(report_text)
    )

    assert applied == 0
    assert warnings
    assert assessment.explanation_source == "deterministic"


def test_grounded_agent_report_marks_explanation_source():
    assessment = evaluate_submission(
        DEMO_SUBMISSIONS[0], "run_agent", as_of=date(2026, 9, 19)
    )
    report_text = _report()["output"][0]["content"][0]["text"]
    applied, warnings = apply_agent_report(
        [assessment], AgentReport.model_validate_json(report_text)
    )

    assert applied == 1
    assert not warnings
    assert assessment.explanation_source == "openai"


def test_grounded_agent_report_surfaces_missing_data_and_contradictions():
    assessment = evaluate_submission(
        DEMO_SUBMISSIONS[0], "run_agent", as_of=date(2026, 9, 19)
    )
    report_text = _report(
        contradictions=["Policy and claim records disagree on the loss date."],
        missing_information=["Confirm the source-system loss date."],
    )["output"][0]["content"][0]["text"]

    applied, warnings = apply_agent_report(
        [assessment], AgentReport.model_validate_json(report_text)
    )

    assert applied == 1
    assert not warnings
    assert assessment.warnings == [
        "Policy and claim records disagree on the loss date."
    ]
    assert "Confirm the source-system loss date." in assessment.missing_information


@pytest.mark.anyio
async def test_service_integrates_agent_without_changing_deterministic_ranking():
    query = {
        "purpose": "Verify policy appetite fields for the queue",
        "query_json": json.dumps(
            {
                "resource": "Policy",
                "select": ["id", "line_of_business", "premium", "tiv", "primary_state"],
                "pagination": {"limit": 25, "offset": 0},
            }
        ),
    }
    transport = ScriptedTransport(
        [
            _call("inspect_schema", "schema-1"),
            _call("get_appetite", "appetite-1"),
            _call("query_federato", "query-1", query),
            _report(),
        ]
    )
    configured = replace(
        settings,
        openai_api_key="test-key",
        openai_model="test-model",
        openai_max_turns=6,
    )
    service = UnderwriteService(configured)
    service.agent = UnderwritingAgent(configured, transport=transport)

    run = await service.analyze(BatchAnalysisRequest())

    assert run.status == "completed"
    assert run.agent_mode == "openai"
    assert run.agent_model == "test-model"
    assert run.assessments[0].status == "target"
    assert {item.status for item in run.assessments} == {
        "target",
        "acceptable",
        "needs_review",
        "out_of_appetite",
    }
    assert any(item.explanation_source == "openai" for item in run.assessments)
    assert any(event.tool == "agent_query_federato" for event in run.trace)


@pytest.mark.anyio
async def test_service_fails_without_fallback_when_openai_fails():
    configured = replace(settings, openai_api_key="test-key")
    service = UnderwriteService(configured)
    service.agent = BrokenAgent()

    run = await service.analyze(BatchAnalysisRequest())

    assert run.status == "failed"
    assert run.agent_mode == "openai_required"
    assert run.assessments == []
    assert any("OpenAI analysis failed" in error for error in run.errors)
    assert any(
        event.tool == "openai_agent" and event.status == "failure"
        for event in run.trace
    )


@pytest.mark.anyio
async def test_agent_reserves_final_bounded_turn_for_report():
    query = {
        "purpose": "Verify policy evidence",
        "query_json": json.dumps(
            {
                "resource": "Policy",
                "select": ["id", "premium"],
                "pagination": {"limit": 10, "offset": 0},
            }
        ),
    }
    transport = ScriptedTransport(
        [
            _call("inspect_schema", "schema-1"),
            _call("get_appetite", "appetite-1"),
            _call("query_federato", "query-1", query),
            _call("query_federato", "query-2", query),
            _call("query_federato", "query-3", query),
            _report(),
        ]
    )
    configured = replace(
        settings,
        openai_api_key="test-key",
        openai_model="test-model",
        openai_max_turns=6,
        openai_max_query_calls=4,
    )
    agent = UnderwritingAgent(configured, transport=transport)
    assessment = evaluate_submission(
        DEMO_SUBMISSIONS[0], "run_agent", as_of=date(2026, 9, 19)
    )

    result = await agent.run(
        assessments=[assessment],
        registry=SchemaRegistry(DEMO_SCHEMA),
        appetite=DEFAULT_APPETITE,
        federato=FederatoClient(configured),
        mode="demo",
        trace=[],
    )

    assert result.report.plan_summary
    assert "tools" not in transport.requests[-1]
    assert "parallel_tool_calls" not in transport.requests[-1]
    assert "Produce the final structured underwriting report" in str(
        transport.requests[-1]["input"][-1]["content"]
    )
