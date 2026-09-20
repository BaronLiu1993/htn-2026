from __future__ import annotations

from datetime import date
from functools import cmp_to_key

from .evidence_ledger import FactMapper, evidence_items
from .guideline_registry import DEFAULT_GUIDELINE, GuidelinePackage
from .models import (
    Assessment,
    EvidenceItem,
    EvidenceLedger,
    RuleOutcome,
    SubmissionEvidence,
    UnderwritingConsideration,
)
from .profile_registry import InvestigationProfile, ProfileRegistry
from .rule_engine import evaluate_package


def _money(value: float | None) -> str:
    if value is None:
        return "unknown"
    if abs(value) >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    return f"${value / 1_000:.0f}K"


def _conflict_outcome(submission: SubmissionEvidence) -> RuleOutcome:
    return RuleOutcome(
        rule_id="source-consistency",
        name="Data consistency",
        kind="requirement",
        state="unresolved",
        actual_value=submission.conflicts,
        expected="No unresolved data conflicts",
        note="Conflicting source observations require human review.",
    )


def build_profile_considerations(
    ledger: EvidenceLedger,
    package: GuidelinePackage,
    profile: InvestigationProfile | None,
) -> list[UnderwritingConsideration]:
    if profile is None:
        return []
    decision_facts = {rule.fact for rule in package.requirements + package.preferences}
    output: list[UnderwritingConsideration] = []
    for domain in profile.domains:
        facts = [ledger.fact(fact_id) for fact_id in domain.fact_ids]
        facts = [fact for fact in facts if fact is not None]
        evidence = [item for fact in facts for item in evidence_items(fact)]
        if facts and all(fact.state == "verified" for fact in facts):
            status = "available"
            summary = f"{domain.label} evidence is available for underwriting review."
        elif evidence:
            status = "partial"
            summary = f"{domain.label} evidence is incomplete."
        else:
            status = "missing"
            summary = f"{domain.label} evidence is not available from the current source plan."
        output.append(
            UnderwritingConsideration(
                category=domain.id,
                label=domain.label,
                status=status,
                summary=summary,
                appetite_rule_applied=bool(set(domain.fact_ids) & decision_facts),
                evidence=evidence,
            )
        )
    return output


def evaluate_ledger(
    submission: SubmissionEvidence,
    ledger: EvidenceLedger,
    run_id: str,
    *,
    package: GuidelinePackage,
    profile: InvestigationProfile | None = None,
) -> Assessment:
    requirements, preferences = evaluate_package(ledger, package)
    if submission.conflicts:
        requirements.append(_conflict_outcome(submission))

    failed = [item for item in requirements if item.state == "failed"]
    unresolved = [item for item in requirements + preferences if item.state == "unresolved"]
    passed = [item for item in requirements if item.state == "passed"]
    matched = [item for item in preferences if item.state == "matched"]
    target_matches = len(matched)
    target_total = len(preferences)
    required_fact_ids = {rule.fact for rule in package.requirements}
    required_facts = [fact for fact in ledger.facts if fact.fact_id in required_fact_ids]
    resolved = sum(fact.state == "verified" for fact in required_facts)
    completeness = resolved / len(required_facts) if required_facts else 1.0
    if submission.conflicts:
        completeness *= 0.875
    completeness = round(completeness, 2)

    if failed:
        status = "out_of_appetite"
        failed_names = ", ".join(item.name.lower() for item in failed[:2])
        explanation = (
            f"Out of appetite because it fails {failed_names}. "
            "Preferences cannot override a hard requirement."
        )
        action = "Deprioritize and confirm the failed requirement before further review"
    elif unresolved:
        status = "needs_review"
        unresolved_names = ", ".join(item.name.lower() for item in unresolved[:2])
        explanation = (
            f"Needs review because {unresolved_names} cannot be resolved from the available "
            "evidence. No hard failure is verified."
        )
        action = "Request the missing or conflicting information"
    elif target_total > 0 and target_matches == target_total:
        status = "target"
        explanation = (
            f"Passes all hard requirements and matches all {target_total} preferences in "
            f"{package.name}."
        )
        action = "Prioritize for underwriting review"
    else:
        status = "acceptable"
        explanation = (
            f"Passes all hard requirements and matches {target_matches} of {target_total} "
            f"preferences. TIV is {_money(submission.tiv)} and premium is {_money(submission.premium)}."
        )
        action = "Keep in the review queue after target submissions"

    missing_information = [
        item.note or f"Resolve {item.name.lower()}"
        for item in unresolved
        if item.kind == "requirement"
    ]
    all_evidence: list[EvidenceItem] = []
    seen: set[tuple[str, str, str]] = set()
    for outcome in requirements + preferences:
        for item in outcome.evidence:
            key = (item.resource, item.record_id, item.field)
            if key not in seen:
                seen.add(key)
                all_evidence.append(item)

    factors = failed or unresolved or matched or passed
    details = []
    for outcome in factors[:2]:
        sources = sorted({f"{item.resource} {item.record_id}" for item in outcome.evidence if item.state == "verified"})
        value = outcome.actual_value
        detail = f"{outcome.name}: {value if value is not None else 'unconfirmed'}"
        if sources:
            detail += f" (source: {', '.join(sources[:2])})"
        details.append(detail)
    match_text = {
        "target": "Matches the guideline's target appetite",
        "acceptable": "Meets the guideline's required appetite",
        "needs_review": "Needs review against the selected guideline",
        "out_of_appetite": "Falls outside the selected guideline's appetite",
    }[status]
    explanation = f"{match_text}. {'; '.join(details)}. {action}."

    considerations = build_profile_considerations(ledger, package, profile)
    return Assessment(
        submission_id=submission.id,
        submission_number=submission.submission_number,
        insured_name=submission.insured_name,
        received_date=submission.received_date,
        status=status,
        target_matches=target_matches,
        target_preferences_total=target_total,
        evidence_completeness=completeness,
        premium=submission.premium,
        tiv=submission.tiv,
        primary_state=submission.primary_state.upper().strip() if submission.primary_state else None,
        matched_preferences=matched,
        passed_requirements=passed,
        failed_requirements=failed,
        unresolved_rules=unresolved,
        missing_information=missing_information,
        recommended_action=action,
        explanation=explanation,
        evidence=all_evidence,
        ledger=ledger,
        profile_considerations=considerations,
        cope=considerations,
        warnings=list(submission.conflicts),
        run_id=run_id,
        guideline_id=package.id,
        guideline_version=package.version,
        guideline_effective_date=package.effective_from,
    )


def evaluate_submission(
    submission: SubmissionEvidence,
    run_id: str,
    *,
    as_of: date | None = None,
    guideline: GuidelinePackage = DEFAULT_GUIDELINE,
    profile: InvestigationProfile | None = None,
) -> Assessment:
    """Compatibility entry point that evaluates one submission through the harness."""
    if profile is None and guideline.investigation_profile_id:
        profile = ProfileRegistry().resolve(guideline.investigation_profile_id)
    ledger = FactMapper(guideline, as_of=as_of).build(submission)
    return evaluate_ledger(submission, ledger, run_id, package=guideline, profile=profile)


def _compare(left: Assessment, right: Assessment, package: GuidelinePackage) -> int:
    status_index = {status: index for index, status in enumerate(package.ranking.status_order)}
    left_status = status_index.get(left.status, len(status_index))
    right_status = status_index.get(right.status, len(status_index))
    if left_status != right_status:
        return -1 if left_status < right_status else 1
    for tie in package.ranking.tie_breakers:
        left_value = getattr(left, tie.field)
        right_value = getattr(right, tie.field)
        if left_value == right_value:
            continue
        if left_value is None:
            return 1
        if right_value is None:
            return -1
        if tie.direction == "asc":
            return -1 if left_value < right_value else 1
        return -1 if left_value > right_value else 1
    return 0


def rank_assessments(
    assessments: list[Assessment],
    package: GuidelinePackage = DEFAULT_GUIDELINE,
) -> list[Assessment]:
    return sorted(assessments, key=cmp_to_key(lambda left, right: _compare(left, right, package)))
