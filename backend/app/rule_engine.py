from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from .appetite_loader import AppetitePack, AppetiteRule
from .models import (
    EvidenceItem,
    RuleOutcome,
    SubmissionEvidence,
    UnderwritingConsideration,
)


ACCEPTABLE_CONSTRUCTION = (
    "joisted masonry",
    "non combustible",
    "noncombustible",
    "steel",
    "masonry non combustible",
    "masonry noncombustible",
)


def normalized(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()


def evidence(
    resource: str,
    record_id: str,
    field: str,
    value: Any,
    label: str,
) -> EvidenceItem:
    return EvidenceItem(
        resource=resource,
        record_id=record_id,
        field=field,
        value=value,
        label=label,
    )


def five_year_cutoff(as_of: date) -> date:
    try:
        return as_of.replace(year=as_of.year - 5)
    except ValueError:
        return as_of.replace(year=as_of.year - 5, day=28)


@dataclass(frozen=True)
class FactValue:
    value: Any
    evidence: list[EvidenceItem]
    ready: bool = True
    note: str | None = None
    display_value: Any = None

    @property
    def actual(self) -> Any:
        return self.display_value if self.display_value is not None else self.value


class EvidenceResolver:
    """Resolve canonical underwriting facts independently of source field names."""

    def __init__(self, submission: SubmissionEvidence, as_of: date) -> None:
        self.submission = submission
        self.as_of = as_of

    def resolve(self, fact: str) -> FactValue:
        method = getattr(self, f"_fact_{fact}", None)
        if method is None:
            return FactValue(None, [], ready=False, note=f'No resolver exists for fact "{fact}".')
        return method()

    def _scalar(
        self,
        value: Any,
        resource: str,
        field: str,
        label: str,
        *,
        transform=lambda item: item,
    ) -> FactValue:
        item = evidence(resource, self.submission.id, field, value, label)
        if value is None or (isinstance(value, str) and not value.strip()):
            return FactValue(None, [item], ready=False)
        return FactValue(transform(value), [item], display_value=value)

    def _fact_submission_type(self) -> FactValue:
        return self._scalar(
            self.submission.submission_type,
            "Submission",
            "submission_type",
            "Submission type",
        )

    def _fact_line_of_business(self) -> FactValue:
        return self._scalar(
            self.submission.line_of_business,
            "Policy",
            "line_of_business",
            "Line of business",
        )

    def _fact_primary_state(self) -> FactValue:
        return self._scalar(
            self.submission.primary_state,
            "Location",
            "state",
            "Primary risk state",
            transform=lambda value: str(value).upper().strip(),
        )

    def _fact_tiv(self) -> FactValue:
        return self._scalar(self.submission.tiv, "Policy", "tiv", "Total insured value")

    def _fact_premium(self) -> FactValue:
        return self._scalar(self.submission.premium, "Policy", "premium", "Total premium")

    def _fact_oldest_building_year(self) -> FactValue:
        items = [
            evidence("Building", item.id, "year_built", item.year_built, "Building year")
            for item in self.submission.buildings
        ]
        years = [item.year_built for item in self.submission.buildings if item.year_built is not None]
        if not self.submission.buildings or len(years) != len(self.submission.buildings):
            return FactValue(
                min(years) if years else None,
                items,
                ready=False,
                note="One or more building years are missing.",
            )
        return FactValue(min(years), items)

    def _fact_acceptable_construction_share(self) -> FactValue:
        items = [
            evidence(
                "Building",
                item.id,
                "construction_type",
                item.construction_type,
                "Construction type",
            )
            for item in self.submission.buildings
        ]
        known = [item for item in self.submission.buildings if item.construction_type]
        if not self.submission.buildings or len(known) != len(self.submission.buildings):
            return FactValue(
                None,
                items,
                ready=False,
                note="One or more construction types are missing.",
            )
        use_tiv_weights = all(item.tiv is not None and item.tiv > 0 for item in known)
        weights = [float(item.tiv) if use_tiv_weights else 1.0 for item in known]
        acceptable_weight = sum(
            weight
            for item, weight in zip(known, weights)
            if any(token in normalized(item.construction_type) for token in ACCEPTABLE_CONSTRUCTION)
        )
        share = acceptable_weight / sum(weights)
        note = (
            "Share is TIV-weighted."
            if use_tiv_weights
            else "Share is based on building count because per-building TIV is unavailable."
        )
        return FactValue(share, items, note=note, display_value=round(share * 100, 1))

    def _fact_five_year_loss_total(self) -> FactValue:
        cutoff = five_year_cutoff(self.as_of)
        items: list[EvidenceItem] = []
        total = 0.0
        ready = True
        for claim in self.submission.claims:
            items.extend(
                [
                    evidence("Claim", claim.id, "loss_date", claim.loss_date, "Loss date"),
                    evidence("Claim", claim.id, "loss_value", claim.loss_value, "Loss value"),
                ]
            )
            if claim.loss_date is None or claim.loss_value is None:
                ready = False
            elif cutoff <= claim.loss_date <= self.as_of:
                total += claim.loss_value
        return FactValue(
            total if ready else None,
            items,
            ready=ready,
            note=f"Window starts {cutoff.isoformat()}." if ready else "One or more claims are incomplete.",
        )


def _matches(operator: str, actual: Any, expected: Any) -> bool:
    if operator == "equals":
        return actual == expected
    if operator == "in":
        return actual in expected
    if operator == "in_normalized":
        return normalized(actual) in {normalized(item) for item in expected}
    if operator == "contains_normalized":
        return normalized(expected) in normalized(actual)
    if operator == "lt":
        return actual < expected
    if operator == "lte":
        return actual <= expected
    if operator == "gt":
        return actual > expected
    if operator == "gte":
        return actual >= expected
    if operator == "between":
        return expected[0] <= actual <= expected[1]
    raise ValueError(f'Unsupported appetite operator "{operator}".')


def evaluate_rule(rule: AppetiteRule, fact: FactValue, kind: str) -> RuleOutcome:
    if not fact.ready:
        state = "unresolved"
    elif fact.value in rule.review_values:
        state = "unresolved"
    else:
        matched = _matches(rule.operator, fact.value, rule.value)
        state = ("passed" if matched else "failed") if kind == "requirement" else (
            "matched" if matched else "not_matched"
        )
    notes = [item for item in (rule.note, fact.note, rule.missing_note if not fact.ready else None) if item]
    return RuleOutcome(
        rule_id=rule.id,
        name=rule.name,
        kind=kind,
        state=state,
        actual_value=fact.actual,
        expected=rule.expected,
        evidence=fact.evidence,
        note=" ".join(dict.fromkeys(notes)) or None,
    )


def evaluate_pack(
    submission: SubmissionEvidence,
    appetite: AppetitePack,
    as_of: date,
) -> tuple[list[RuleOutcome], list[RuleOutcome]]:
    resolver = EvidenceResolver(submission, as_of)
    requirements = [
        evaluate_rule(rule, resolver.resolve(rule.fact), "requirement")
        for rule in appetite.requirements
    ]
    preferences = [
        evaluate_rule(rule, resolver.resolve(rule.fact), "preference")
        for rule in appetite.preferences
    ]
    return requirements, preferences


def build_cope_summary(
    submission: SubmissionEvidence,
    requirements: list[RuleOutcome],
) -> list[UnderwritingConsideration]:
    construction_rule = next((item for item in requirements if item.rule_id == "R7"), None)
    construction_evidence = construction_rule.evidence if construction_rule else []
    construction_status = "available" if construction_evidence and all(
        item.value not in (None, "") for item in construction_evidence
    ) else "missing"

    occupancy_evidence = [
        evidence("Building", item.id, "occupancy", item.occupancy, "Occupancy")
        for item in submission.buildings
        if item.occupancy
    ]
    protection_evidence: list[EvidenceItem] = []
    exposure_evidence: list[EvidenceItem] = []
    for item in submission.buildings:
        if item.sprinklered is not None:
            protection_evidence.append(
                evidence("Building", item.id, "sprinklered", item.sprinklered, "Sprinklered")
            )
        if item.protection_class:
            protection_evidence.append(
                evidence(
                    "Building", item.id, "protection_class", item.protection_class, "Protection class"
                )
            )
        if item.flood_zone:
            exposure_evidence.append(
                evidence("Building", item.id, "flood_zone", item.flood_zone, "Flood zone")
            )
        if item.wildfire_score is not None:
            exposure_evidence.append(
                evidence(
                    "Building", item.id, "wildfire_score", item.wildfire_score, "Wildfire score"
                )
            )
    if submission.primary_state:
        exposure_evidence.insert(
            0,
            evidence("Location", submission.id, "state", submission.primary_state, "Primary risk state"),
        )

    return [
        UnderwritingConsideration(
            category="construction",
            status=construction_status,
            summary=(
                "Construction evidence is used by an approved appetite rule."
                if construction_status == "available"
                else "Construction details are incomplete."
            ),
            appetite_rule_applied=True,
            evidence=construction_evidence,
        ),
        UnderwritingConsideration(
            category="occupancy",
            status="available" if occupancy_evidence else "missing",
            summary=(
                "Occupancy evidence is available for underwriting review; no carrier decision rule was supplied."
                if occupancy_evidence
                else "Actual building occupancy was not available; line of business is not treated as a substitute."
            ),
            evidence=occupancy_evidence,
        ),
        UnderwritingConsideration(
            category="protection",
            status="available" if protection_evidence else "missing",
            summary=(
                "Protection evidence is available for underwriting review; no carrier decision rule was supplied."
                if protection_evidence
                else "Sprinkler and protection-class evidence was not available."
            ),
            evidence=protection_evidence,
        ),
        UnderwritingConsideration(
            category="exposure",
            status="available" if len(exposure_evidence) > 1 else "partial",
            summary=(
                "Hazard exposure evidence is available; no carrier decision rule was supplied."
                if len(exposure_evidence) > 1
                else "State is available, but detailed flood, wildfire, wind, or nearby-hazard evidence is not."
            ),
            appetite_rule_applied=False,
            evidence=exposure_evidence,
        ),
    ]
