from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from .config import Settings
from .guideline_registry import GuidelinePackage
from .models import Assessment, EvidenceLedger, SubmissionEvidence


SYSTEM_PROMPT = """You are an appetite-driven commercial insurance underwriting classifier.

Apply only the supplied appetite profile to the supplied policy facts. Do not use outside knowledge or infer missing values.

Decision order: first decline for any evidenced hard-rule failure; otherwise return insufficient_information for a required missing, invalid, or contradictory fact; otherwise refer only for an evidenced explicit referral rule; otherwise accept.

Return JSON only, with exactly this schema and no additional keys:
{"expected_disposition":"accept | refer | decline | insufficient_information","expected_rules":["stable_rule_category"]}

Use building_age for building-year rules, loss_history for five-year loss rules, hazard_exposure for hazard rules, and primary_risk_location for multistate location requirements. Use an empty array when no rule category determines the result.

/no_think"""


class BasetenDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_disposition: str
    expected_rules: list[str]


@dataclass
class BasetenPrediction:
    submission_id: str
    decision: BasetenDecision
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int


@dataclass
class BasetenRunResult:
    model: str
    predictions: list[BasetenPrediction]
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int
    agreement_rate: float


def _fact(ledger: EvidenceLedger, fact_id: str) -> Any:
    item = ledger.fact(fact_id)
    return item.value if item and item.state == "verified" else None


def _rules(package: GuidelinePackage) -> dict[str, Any]:
    rules: dict[str, Any] = {}
    for rule in package.requirements:
        if rule.fact == "submission_type" and rule.operator in {"in", "in_normalized"}:
            rules["business_types"] = rule.value
        elif rule.fact == "primary_state" and rule.operator == "in":
            rules["eligible_states"] = rule.value
        elif rule.fact == "tiv" and rule.operator == "lte":
            rules["total_tiv_max"] = rule.value
        elif rule.fact == "premium" and rule.operator == "between":
            rules["premium"] = {"min": rule.value[0], "max": rule.value[1]}
        elif rule.fact == "oldest_building_year" and rule.operator == "gt":
            rules["building_year_exclusive_min"] = rule.value
        elif rule.fact == "five_year_loss_total" and rule.operator == "lt":
            rules["loss_value_5yr_max"] = float(rule.value) - 0.01
    return rules


def _payload(
    submission: SubmissionEvidence,
    ledger: EvidenceLedger,
    package: GuidelinePackage,
) -> dict[str, Any]:
    location_count = sum(
        len(rows)
        for resource, rows in submission.raw_records.items()
        if "location" in resource.lower()
    )
    primary_state = _fact(ledger, "primary_state")
    policy = {
        "premium": _fact(ledger, "premium"),
        "business_type": _fact(ledger, "submission_type"),
        "states": [primary_state] if primary_state else None,
        "total_tiv": _fact(ledger, "tiv"),
        "min_building_year": _fact(ledger, "oldest_building_year"),
        "loss_value_5yr": _fact(ledger, "five_year_loss_total"),
        "location_count": location_count or None,
        "hazards": [],
        "line_of_business": _fact(ledger, "line_of_business"),
    }
    appetite = {
        "appetite_id": f"{package.id}-{package.version}",
        "name": package.name,
        "strategy": package.scope.description,
        "rules": _rules(package),
        "on_missing_required_data": "insufficient_information",
    }
    return {
        "task": "evaluate_appetite",
        "schema_version": "underwriting-sft-v2",
        "benchmark_schema_version": "1.0",
        "appetite": appetite,
        "policy": policy,
    }


def _expected_disposition(assessment: Assessment) -> str:
    if assessment.status == "out_of_appetite":
        return "decline"
    if assessment.status == "needs_review":
        return "insufficient_information"
    return "accept"


class BasetenUnderwriter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def configured(self) -> bool:
        return bool(
            self.settings.baseten_model_id
            and Path(self.settings.baseten_cli_path).is_file()
        )

    async def _predict(
        self,
        submission: SubmissionEvidence,
        ledger: EvidenceLedger,
        package: GuidelinePackage,
        semaphore: asyncio.Semaphore,
    ) -> BasetenPrediction:
        request = {
            "model": ".",
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(_payload(submission, ledger, package), sort_keys=True),
                },
            ],
            "temperature": 0,
            "max_tokens": 128,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        async with semaphore:
            started = time.perf_counter()
            process = await asyncio.create_subprocess_exec(
                self.settings.baseten_cli_path,
                "model",
                "predict",
                "--model-id",
                self.settings.baseten_model_id,
                "--file",
                "-",
                "-o",
                "json",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(json.dumps(request).encode()),
                    timeout=self.settings.baseten_request_timeout_seconds,
                )
            except TimeoutError as exc:
                process.kill()
                await process.communicate()
                raise RuntimeError(
                    "The UnderwriteIQ model request exceeded the configured timeout."
                ) from exc
        if process.returncode != 0:
            message = stderr.decode().strip() or "Baseten CLI request failed."
            raise RuntimeError(message[:500])
        response = json.loads(stdout)
        content = response["choices"][0]["message"]["content"]
        decision = BasetenDecision.model_validate_json(content)
        if decision.expected_disposition not in {
            "accept", "refer", "decline", "insufficient_information"
        }:
            raise ValueError("Baseten returned an unsupported disposition.")
        usage = response.get("usage") or {}
        return BasetenPrediction(
            submission_id=submission.id,
            decision=decision,
            latency_ms=max(1, int((time.perf_counter() - started) * 1000)),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
        )

    async def run(
        self,
        submissions: list[SubmissionEvidence],
        ledgers: list[EvidenceLedger],
        assessments: list[Assessment],
        package: GuidelinePackage,
    ) -> BasetenRunResult:
        semaphore = asyncio.Semaphore(self.settings.baseten_max_concurrency)
        started = time.perf_counter()
        predictions = await asyncio.gather(
            *(
                self._predict(submission, ledger, package, semaphore)
                for submission, ledger in zip(submissions, ledgers)
            )
        )
        expected = {
            assessment.submission_id: _expected_disposition(assessment)
            for assessment in assessments
        }
        agreed = sum(
            prediction.decision.expected_disposition == expected[prediction.submission_id]
            for prediction in predictions
        )
        return BasetenRunResult(
            model=self.settings.baseten_model_name,
            predictions=predictions,
            latency_ms=max(1, int((time.perf_counter() - started) * 1000)),
            prompt_tokens=sum(item.prompt_tokens for item in predictions),
            completion_tokens=sum(item.completion_tokens for item in predictions),
            agreement_rate=agreed / len(predictions) if predictions else 1.0,
        )
