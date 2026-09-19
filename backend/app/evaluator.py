from __future__ import annotations

from datetime import date

from .appetite_loader import AppetitePack, DEFAULT_APPETITE
from .models import Assessment, EvidenceItem, RuleOutcome, SubmissionEvidence
from .rule_engine import build_cope_summary, evaluate_pack


def _money(value: float | None) -> str:
    if value is None:
        return "unknown"
    if abs(value) >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    return f"${value / 1_000:.0f}K"


def _conflict_outcome(submission: SubmissionEvidence) -> RuleOutcome:
    return RuleOutcome(
        rule_id="R0",
        name="Data consistency",
        kind="requirement",
        state="unresolved",
        actual_value=submission.conflicts,
        expected="No unresolved data conflicts",
        note="Conflicting or malformed source data requires human review.",
    )


def evaluate_submission(
    submission: SubmissionEvidence,
    run_id: str,
    *,
    as_of: date | None = None,
    appetite: AppetitePack = DEFAULT_APPETITE,
) -> Assessment:
    """Evaluate canonical Federato evidence against a versioned carrier rule pack."""

    requirements, preferences = evaluate_pack(submission, appetite, as_of or date.today())
    if submission.conflicts:
        requirements.append(_conflict_outcome(submission))

    failed = [item for item in requirements if item.state == "failed"]
    unresolved = [item for item in requirements + preferences if item.state == "unresolved"]
    passed = [item for item in requirements if item.state == "passed"]
    matched = [item for item in preferences if item.state == "matched"]
    target_matches = len(matched)
    target_total = len(preferences)

    core_requirements = [item for item in requirements if item.rule_id != "R0"]
    resolved_core = len([item for item in core_requirements if item.state != "unresolved"])
    completeness = resolved_core / len(core_requirements)
    if submission.conflicts:
        completeness *= 0.875
    completeness = round(completeness, 2)

    if failed:
        status = "out_of_appetite"
        failed_names = ", ".join(item.name.lower() for item in failed[:2])
        explanation = (
            f"Out of appetite because it fails {failed_names}. "
            "Target preferences cannot override a hard requirement."
        )
        action = "Deprioritize and confirm the failed requirement before further review"
    elif unresolved:
        status = "needs_review"
        unresolved_names = ", ".join(item.name.lower() for item in unresolved[:2])
        explanation = (
            f"Needs review because {unresolved_names} cannot be resolved from the available "
            "evidence. No hard failure is currently verified."
        )
        action = "Request the missing or conflicting information"
    elif target_total > 0 and target_matches == target_total:
        status = "target"
        explanation = (
            f"Passes all hard requirements and matches all {target_total} target preferences "
            "in the active carrier appetite."
        )
        action = "Prioritize for underwriting review"
    else:
        status = "acceptable"
        explanation = (
            f"Passes all hard requirements and matches {target_matches} of {target_total} target "
            f"preferences. TIV is {_money(submission.tiv)} and premium is "
            f"{_money(submission.premium)}."
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
        cope=build_cope_summary(submission, requirements),
        warnings=list(submission.conflicts),
        run_id=run_id,
        appetite_id=appetite.id,
        appetite_version=appetite.version,
        appetite_effective_date=appetite.effective_from,
    )


STATUS_ORDER = {
    "target": 0,
    "acceptable": 1,
    "needs_review": 2,
    "out_of_appetite": 3,
}


def rank_assessments(assessments: list[Assessment]) -> list[Assessment]:
    return sorted(
        assessments,
        key=lambda item: (
            STATUS_ORDER[item.status],
            -item.target_matches,
            -item.evidence_completeness,
            item.received_date or date.max,
            item.submission_id,
        ),
    )
