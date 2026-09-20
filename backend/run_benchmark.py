#!/usr/bin/env python3
"""Run the appetite benchmark against a configured OpenAI-compatible model."""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv
from openai import OpenAI


BACKEND = Path(__file__).resolve().parent
BENCHMARK = BACKEND / "benchmark"
DEFAULT_OPENAI_MODEL = "gpt-5.6-sol"
DEFAULT_BASETEN_MODEL = "baseten-model"
VALID_DISPOSITIONS = {"accept", "refer", "decline", "insufficient_information"}
RULE_ALIASES = {
    "loss_history": "loss_value",
    "hazard_exposure": "hazard",
    "claims_status": "claims",
    "multistate_exposure": "multistate",
}

# Supports `python3 backend/run_benchmark.py` without manually exporting secrets.
load_dotenv(BACKEND / ".env", override=False)

SYSTEM_PROMPT = """You are an appetite-driven commercial insurance underwriting classifier.

Apply only the supplied appetite profile to the supplied policy facts. Do not use
outside knowledge or infer missing values. First decline for any evidenced hard-rule
failure; otherwise return insufficient_information for a required missing or invalid
fact; otherwise refer only for an evidenced explicit referral rule; otherwise accept.
A min or max threshold is inclusive. A building-year exclusive minimum requires a
strictly greater value. A restricted-state rule fails when any policy state is ineligible.

Return only the requested JSON. Rules must use only these benchmark categories:
state, premium, total_tiv, building_age, loss_value, business_type, location_count,
hazard, primary_risk_location, multistate, claims. List every category that determines
the disposition. Conditions are only applicable to a referral and must exactly copy
applicable values from the supplied appetite's rules.conditions list.
"""

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "disposition": {"type": "string", "enum": sorted(VALID_DISPOSITIONS)},
        "rules": {"type": "array", "items": {"type": "string"}},
        "conditions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["disposition", "rules", "conditions"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    model: str
    api_key: str
    base_url: str
    reasoning_effort: str


@dataclass(frozen=True)
class ModelPrediction:
    disposition: str
    rules: list[str]
    conditions: list[str]
    response_model: str | None = None
    usage: dict[str, int] = field(default_factory=dict)


@dataclass
class CaseResult:
    policy_id: str
    appetite_id: str
    expected_disposition: str
    actual_disposition: str | None
    expected_rules: list[str]
    actual_rules: list[str]
    expected_conditions: list[str]
    actual_conditions: list[str]
    latency_ms: int
    response_model: str | None = None
    error: str | None = None

    @property
    def disposition_correct(self) -> bool:
        return self.expected_disposition == self.actual_disposition

    @property
    def rules_exact(self) -> bool:
        return set(self.expected_rules) == set(self.actual_rules)

    @property
    def conditions_exact(self) -> bool:
        return set(self.expected_conditions) == set(self.actual_conditions)


PredictionRunner = Callable[[dict[str, Any], dict[str, Any]], ModelPrediction]


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def model_config_from_env() -> ModelConfig:
    """Run OpenAI by default; select Baseten explicitly for comparison runs."""

    provider = os.getenv("BENCHMARK_PROVIDER", "openai").lower()
    if provider not in {"openai", "baseten"}:
        raise ValueError("BENCHMARK_PROVIDER must be either 'openai' or 'baseten'.")
    baseten_key = os.getenv("BASETEN_API_KEY")
    baseten_url = os.getenv("BASETEN_MODEL_URL")
    if bool(baseten_key) != bool(baseten_url):
        raise ValueError("BASETEN_API_KEY and BASETEN_MODEL_URL must be set together.")
    if provider == "baseten":
        if not baseten_key or not baseten_url:
            raise ValueError(
                "BASETEN_API_KEY and BASETEN_MODEL_URL must be set when "
                "BENCHMARK_PROVIDER=baseten."
            )
        return ModelConfig(
            provider="baseten",
            model=os.getenv("BASETEN_MODEL", DEFAULT_BASETEN_MODEL),
            api_key=baseten_key,
            base_url=baseten_url.rstrip("/"),
            reasoning_effort=os.getenv("OPENAI_REASONING_EFFORT", "medium"),
        )

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError(
            "OPENAI_API_KEY must be set to run GPT-5.6 Sol. To use Baseten, set "
            "BENCHMARK_PROVIDER=baseten plus both Baseten environment variables."
        )
    return ModelConfig(
        provider="openai",
        model=os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL),
        api_key=api_key,
        base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
        reasoning_effort=os.getenv("OPENAI_REASONING_EFFORT", "medium"),
    )


def _f1(expected: list[str], actual: list[str]) -> float:
    left, right = set(expected), set(actual)
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    precision = len(left & right) / len(right)
    recall = len(left & right) / len(left)
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def _normalise_rules(value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("model response field 'rules' must be an array of strings")
    return list(dict.fromkeys(RULE_ALIASES.get(item, item) for item in value))


def _normalise_conditions(value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("model response field 'conditions' must be an array of strings")
    return list(dict.fromkeys(value))


def _prediction_from_content(content: str, response_model: str | None, usage: Any) -> ModelPrediction:
    try:
        result = json.loads(content)
    except json.JSONDecodeError as error:
        raise ValueError(f"model returned invalid JSON: {error.msg}") from error
    if not isinstance(result, dict):
        raise ValueError("model response must be a JSON object")
    disposition = result.get("disposition")
    if disposition not in VALID_DISPOSITIONS:
        raise ValueError(f"model returned invalid disposition: {disposition!r}")
    usage_data = {
        "prompt_tokens": int(
            getattr(usage, "prompt_tokens", getattr(usage, "input_tokens", 0)) or 0
        ),
        "completion_tokens": int(
            getattr(usage, "completion_tokens", getattr(usage, "output_tokens", 0)) or 0
        ),
        "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
    }
    return ModelPrediction(
        disposition=disposition,
        rules=_normalise_rules(result.get("rules")),
        conditions=_normalise_conditions(result.get("conditions")),
        response_model=response_model,
        usage=usage_data,
    )


def build_prediction_runner(config: ModelConfig) -> PredictionRunner:
    """Build a real request function for OpenAI or an OpenAI-compatible Baseten URL."""

    client = OpenAI(api_key=config.api_key, base_url=config.base_url, timeout=180.0, max_retries=1)

    def predict(policy: dict[str, Any], appetite: dict[str, Any]) -> ModelPrediction:
        payload = {
            "task": "evaluate_appetite",
            "appetite": appetite,
            "policy": policy["facts"],
        }
        if config.provider == "openai":
            response = client.responses.create(
                model=config.model,
                instructions=SYSTEM_PROMPT,
                input=json.dumps(payload, sort_keys=True),
                store=False,
                reasoning={"effort": config.reasoning_effort},
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "underwriting_benchmark_prediction",
                        "strict": True,
                        "schema": OUTPUT_SCHEMA,
                    }
                },
            )
            content = response.output_text
        else:
            response = client.chat.completions.create(
                model=config.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(payload, sort_keys=True)},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "underwriting_benchmark_prediction",
                        "strict": True,
                        "schema": OUTPUT_SCHEMA,
                    },
                },
            )
            content = response.choices[0].message.content
        if not content:
            raise ValueError("model returned an empty response")
        return _prediction_from_content(content, response.model, response.usage)

    return predict


def run(
    policies_doc: dict[str, Any],
    appetites_doc: dict[str, Any],
    matrix_doc: dict[str, Any],
    model_config: ModelConfig | None = None,
    predict: PredictionRunner | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Invoke the model for every matrix cell and score returned predictions."""

    model_config = model_config or model_config_from_env()
    predict = predict or build_prediction_runner(model_config)
    policies = {item["policy_id"]: item for item in policies_doc["policies"]}
    appetites = {item["appetite_id"]: item for item in appetites_doc["appetites"]}
    evaluations = matrix_doc["live_policy_evaluations"] + matrix_doc["synthetic_policy_evaluations"]
    if limit is not None:
        evaluations = evaluations[:limit]
    results: list[CaseResult] = []
    usage_totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    for expected in evaluations:
        policy = policies[expected["policy_id"]]
        appetite = appetites[expected["appetite_id"]]
        started = time.monotonic()
        try:
            prediction = predict(policy, appetite)
            for key in usage_totals:
                usage_totals[key] += prediction.usage.get(key, 0)
            result = CaseResult(
                policy_id=expected["policy_id"],
                appetite_id=expected["appetite_id"],
                expected_disposition=expected["expected_disposition"],
                actual_disposition=prediction.disposition,
                expected_rules=expected.get("expected_rules", []),
                actual_rules=prediction.rules,
                expected_conditions=expected.get("expected_conditions", []),
                actual_conditions=prediction.conditions,
                response_model=prediction.response_model,
                latency_ms=round((time.monotonic() - started) * 1000),
            )
        except Exception as error:
            result = CaseResult(
                policy_id=expected["policy_id"],
                appetite_id=expected["appetite_id"],
                expected_disposition=expected["expected_disposition"],
                actual_disposition=None,
                expected_rules=expected.get("expected_rules", []),
                actual_rules=[],
                expected_conditions=expected.get("expected_conditions", []),
                actual_conditions=[],
                latency_ms=round((time.monotonic() - started) * 1000),
                error=f"{type(error).__name__}: {error}",
            )
        results.append(result)

    total = len(results)
    exact = sum(
        item.disposition_correct and item.rules_exact and item.conditions_exact for item in results
    )
    return {
        "benchmark_id": matrix_doc["benchmark_id"],
        "started_at": datetime.now(UTC).isoformat(),
        "provider": model_config.provider,
        "model": model_config.model,
        "base_url": model_config.base_url,
        "case_count": total,
        "failed_case_count": sum(item.error is not None for item in results),
        "usage": usage_totals,
        "scores": {
            "disposition_accuracy": round(sum(item.disposition_correct for item in results) / total, 4),
            "rule_attribution_f1": round(
                sum(
                    _f1(item.expected_rules, item.actual_rules) if item.error is None else 0.0
                    for item in results
                )
                / total,
                4,
            ),
            "condition_f1": round(
                sum(
                    _f1(item.expected_conditions, item.actual_conditions) if item.error is None else 0.0
                    for item in results
                )
                / total,
                4,
            ),
            "full_case_exact_match": round(exact / total, 4),
        },
        "results": [
            {
                "policy_id": item.policy_id,
                "appetite_id": item.appetite_id,
                "expected_disposition": item.expected_disposition,
                "actual_disposition": item.actual_disposition,
                "disposition_correct": item.disposition_correct,
                "expected_rules": item.expected_rules,
                "actual_rules": item.actual_rules,
                "rules_exact": item.rules_exact,
                "expected_conditions": item.expected_conditions,
                "actual_conditions": item.actual_conditions,
                "conditions_exact": item.conditions_exact,
                "latency_ms": item.latency_ms,
                "response_model": item.response_model,
                "error": item.error,
            }
            for item in results
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Write the complete JSON report to this path.")
    parser.add_argument(
        "--limit",
        type=int,
        help="Evaluate only the first N benchmark cases.",
    )
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be a positive integer")
    try:
        config = model_config_from_env()
    except ValueError as error:
        parser.error(str(error))

    report = run(
        load_json(BENCHMARK / "policies.json"),
        load_json(BENCHMARK / "appetites.json"),
        load_json(BENCHMARK / "evaluation-matrix.json"),
        config,
        limit=args.limit,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    scores = report["scores"]
    print(f"benchmark={report['benchmark_id']}")
    print(f"provider={report['provider']}")
    print(f"model={report['model']}")
    print(f"cases={report['case_count']}")
    print(f"failed_cases={report['failed_case_count']}")
    print(f"disposition_accuracy={scores['disposition_accuracy']:.4f}")
    print(f"rule_attribution_f1={scores['rule_attribution_f1']:.4f}")
    print(f"condition_f1={scores['condition_f1']:.4f}")
    print(f"full_case_exact_match={scores['full_case_exact_match']:.4f}")
    if args.output:
        print(f"report={args.output}")
    return 1 if report["failed_case_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
