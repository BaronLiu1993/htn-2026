from __future__ import annotations

from typing import Any, Literal

from .evidence_ledger import evidence_items, normalized
from .guideline_registry import GuidelinePackage, GuidelineRule, ScopePredicate
from .models import EvidenceFact, EvidenceLedger, RuleOutcome


def matches(operator: str, actual: Any, expected: Any) -> bool:
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
    raise ValueError(f'Unsupported guideline operator "{operator}".')


def scope_matches(scope: ScopePredicate, ledger: EvidenceLedger) -> bool:
    fact = ledger.fact(scope.fact)
    return bool(fact and fact.state == "verified" and matches(scope.operator, fact.value, scope.value))


def scope_status(
    scope: ScopePredicate, ledger: EvidenceLedger
) -> Literal["applicable", "not_applicable", "not_evaluated"]:
    fact = ledger.fact(scope.fact)
    if fact is None or fact.state != "verified":
        return "not_evaluated"
    return (
        "applicable"
        if matches(scope.operator, fact.value, scope.value)
        else "not_applicable"
    )


def evaluate_rule(rule: GuidelineRule, fact: EvidenceFact | None, kind: str) -> RuleOutcome:
    ready = fact is not None and fact.state == "verified"
    value = fact.value if fact else None
    if not ready:
        state = "unresolved"
    elif value in rule.review_values:
        state = "unresolved"
    else:
        matched = matches(rule.operator, value, rule.value)
        state = ("passed" if matched else "failed") if kind == "requirement" else (
            "matched" if matched else "not_matched"
        )
    notes = [
        item
        for item in (
            rule.note,
            fact.note if fact else None,
            rule.missing_note if not ready else None,
        )
        if item
    ]
    actual = value
    return RuleOutcome(
        rule_id=rule.id,
        name=rule.name,
        kind=kind,
        state=state,
        actual_value=actual,
        expected=rule.expected,
        evidence=evidence_items(fact) if fact else [],
        note=" ".join(dict.fromkeys(notes)) or None,
    )


def evaluate_package(
    ledger: EvidenceLedger,
    package: GuidelinePackage,
) -> tuple[list[RuleOutcome], list[RuleOutcome]]:
    requirements = [
        evaluate_rule(rule, ledger.fact(rule.fact), "requirement")
        for rule in package.requirements
    ]
    preferences = [
        evaluate_rule(rule, ledger.fact(rule.fact), "preference")
        for rule in package.preferences
    ]
    return requirements, preferences
