"""Generic, deterministic evaluator for versioned appetite benchmark profiles."""

from __future__ import annotations

from typing import Any, Mapping


BENCHMARK_SCHEMA_VERSION = "1.0"
BASE_REQUIRED_FACTS = (
    "premium",
    "business_type",
    "states",
    "total_tiv",
    "min_building_year",
    "loss_value_5yr",
    "hazards",
)
MODEL_RULE_ALIASES = {
    "business_types": "business_type",
    "eligible_states": "state",
    "premium": "premium",
    "total_tiv_max": "total_tiv",
    "building_year_exclusive_min": "building_age",
    "refer_if_building_year_at_or_below": "building_age",
    "loss_value_5yr_max": "loss_history",
    "location_count_max": "location_count",
    "open_or_litigated_claims_allowed": "claims_status",
    "prohibited_hazards": "hazard_exposure",
    "refer_hazards": "hazard_exposure",
    "refer_if_multistate": "multistate_exposure",
    "require_primary_risk_location_for_multistate": "primary_risk_location",
}
MISSING_FIELD_RULES = {
    "business_type": "business_type",
    "states": "state",
    "primary_risk_state": "primary_risk_location",
    "premium": "premium",
    "total_tiv": "total_tiv",
    "min_building_year": "building_age",
    "loss_value_5yr": "loss_history",
    "location_count": "location_count",
    "hazards": "hazard_exposure",
    "open_claim_count": "claims_status",
    "litigated_claim_count": "claims_status",
}


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").replace("$", "").strip())
    except ValueError:
        return None


def _text_set(value: Any) -> set[str] | None:
    if value is None:
        return None
    values = value if isinstance(value, list) else [value]
    return {str(item).strip().lower() for item in values if str(item).strip()}


def _evidence(policy: Mapping[str, Any], field: str, rule_id: str) -> dict[str, Any] | None:
    if policy.get(field) in (None, ""):
        return None
    return {"path": f"policy.{field}", "value": policy[field], "rule_ids": [rule_id]}


def _required_fields(rules: Mapping[str, Any], policy: Mapping[str, Any]) -> tuple[str, ...]:
    fields = list(BASE_REQUIRED_FACTS)
    if "location_count_max" in rules:
        fields.append("location_count")
    states = _text_set(policy.get("states"))
    if rules.get("require_primary_risk_location_for_multistate") and states is not None and len(states) > 1:
        fields.append("primary_risk_state")
    if rules.get("open_or_litigated_claims_allowed") is False:
        fields.extend(("open_claim_count", "litigated_claim_count"))
    return tuple(fields)


def evaluate_appetite(policy: Mapping[str, Any], appetite: Mapping[str, Any]) -> dict[str, Any]:
    """Apply one profile to one policy without using model judgement.

    A hard appetite failure takes precedence over missing data. Referral rules are
    considered only after the profile's hard eligibility rules have passed.
    """

    appetite_id = str(appetite["appetite_id"])
    rules = appetite.get("rules")
    if not isinstance(rules, Mapping):
        raise ValueError(f"Appetite {appetite_id} requires an object-valued rules field")

    missing_fields = [field for field in _required_fields(rules, policy) if policy.get(field) in (None, "")]
    hard_failures: list[dict[str, Any]] = []
    driving_rules: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    target_matches: list[str] = []
    referral_rules: list[dict[str, Any]] = []

    def add_rule(rule_id: str, outcome: str, detail: str, *fields: str) -> None:
        paths = [f"policy.{field}" for field in fields]
        driving_rules.append({"rule_id": rule_id, "outcome": outcome, "detail": detail, "evidence_paths": paths})
        for field in fields:
            item = _evidence(policy, field, rule_id)
            if item is not None:
                evidence.append(item)

    def decline(rule_id: str, detail: str, *fields: str) -> None:
        hard_failures.append({"rule_id": rule_id, "detail": detail, "evidence_paths": [f"policy.{field}" for field in fields]})
        add_rule(rule_id, "not_acceptable", detail, *fields)

    business_types = _text_set(rules.get("business_types"))
    business_type = str(policy.get("business_type", "")).strip().lower()
    if business_types is not None and business_type:
        if business_type not in business_types:
            decline("business_types", "Business type is not eligible for this appetite.", "business_type")
        else:
            add_rule("business_types", "acceptable", "Business type is eligible for this appetite.", "business_type")

    states = _text_set(policy.get("states"))
    eligible_states = rules.get("eligible_states")
    if states is not None and eligible_states != "US":
        allowed_states = _text_set(eligible_states) or set()
        outside = sorted(states - allowed_states)
        if outside:
            decline("eligible_states", f"States outside appetite: {', '.join(state.upper() for state in outside)}.", "states")
        else:
            add_rule("eligible_states", "acceptable", "All policy states are eligible.", "states")
    elif states is not None:
        add_rule("eligible_states", "acceptable", "Nationwide eligibility applies.", "states")

    premium = _number(policy.get("premium"))
    premium_rule = rules.get("premium")
    if premium is not None and isinstance(premium_rule, Mapping):
        minimum, maximum = _number(premium_rule.get("min")), _number(premium_rule.get("max"))
        if (minimum is not None and premium < minimum) or (maximum is not None and premium > maximum):
            decline("premium", "Premium is outside the appetite range.", "premium")
        else:
            add_rule("premium", "acceptable", "Premium is within the appetite range.", "premium")

    total_tiv = _number(policy.get("total_tiv"))
    tiv_max = _number(rules.get("total_tiv_max"))
    if total_tiv is not None and tiv_max is not None:
        if total_tiv > tiv_max:
            decline("total_tiv_max", "Total TIV exceeds the appetite maximum.", "total_tiv")
        else:
            add_rule("total_tiv_max", "acceptable", "Total TIV is within the appetite maximum.", "total_tiv")

    building_year = _number(policy.get("min_building_year"))
    year_min = _number(rules.get("building_year_exclusive_min"))
    if building_year is not None and year_min is not None:
        if building_year <= year_min:
            decline("building_year_exclusive_min", "Building year does not meet the exclusive minimum.", "min_building_year")
        else:
            add_rule("building_year_exclusive_min", "acceptable", "Building year meets the exclusive minimum.", "min_building_year")

    loss_value = _number(policy.get("loss_value_5yr"))
    loss_max = _number(rules.get("loss_value_5yr_max"))
    if loss_value is not None and loss_max is not None:
        if loss_value > loss_max:
            decline("loss_value_5yr_max", "Five-year loss value exceeds the appetite maximum.", "loss_value_5yr")
        else:
            add_rule("loss_value_5yr_max", "acceptable", "Five-year loss value is within the appetite maximum.", "loss_value_5yr")

    location_count = _number(policy.get("location_count"))
    location_max = _number(rules.get("location_count_max"))
    if location_count is not None and location_max is not None:
        if location_count > location_max:
            decline("location_count_max", "Location count exceeds the appetite maximum.", "location_count")
        else:
            add_rule("location_count_max", "acceptable", "Location count is within the appetite maximum.", "location_count")

    if rules.get("open_or_litigated_claims_allowed") is False:
        open_claims = _number(policy.get("open_claim_count"))
        litigated_claims = _number(policy.get("litigated_claim_count"))
        if open_claims is not None and litigated_claims is not None:
            if open_claims > 0 or litigated_claims > 0:
                decline("open_or_litigated_claims_allowed", "Open or litigated claims are not allowed.", "open_claim_count", "litigated_claim_count")
            else:
                add_rule("open_or_litigated_claims_allowed", "acceptable", "No open or litigated claims are reported.", "open_claim_count", "litigated_claim_count")

    hazards = _text_set(policy.get("hazards"))
    prohibited_hazards = _text_set(rules.get("prohibited_hazards")) or set()
    if hazards is not None and hazards & prohibited_hazards:
        names = ", ".join(sorted(hazards & prohibited_hazards))
        decline("prohibited_hazards", f"Prohibited hazards are present: {names}.", "hazards")
    elif hazards is not None and prohibited_hazards:
        add_rule("prohibited_hazards", "acceptable", "No prohibited hazards are present.", "hazards")

    refer_hazards = _text_set(rules.get("refer_hazards")) or set()
    if hazards is not None and hazards & refer_hazards:
        names = ", ".join(sorted(hazards & refer_hazards))
        referral_rules.append({"rule_id": "refer_hazards", "detail": f"Referral hazards are present: {names}.", "fields": ("hazards",)})

    refer_year = _number(rules.get("refer_if_building_year_at_or_below"))
    if building_year is not None and refer_year is not None and building_year <= refer_year:
        referral_rules.append({"rule_id": "refer_if_building_year_at_or_below", "detail": "Building year requires mitigation review.", "fields": ("min_building_year",)})

    if rules.get("refer_if_multistate") and states is not None and len(states) > 1:
        referral_rules.append({"rule_id": "refer_if_multistate", "detail": "Multistate exposure requires specialist review.", "fields": ("states",)})

    if rules.get("require_primary_risk_location_for_multistate") and states is not None and len(states) > 1:
        primary_state = str(policy.get("primary_risk_state", "")).strip().lower()
        if primary_state:
            add_rule("require_primary_risk_location_for_multistate", "acceptable", "Primary risk location is provided for the multistate policy.", "states", "primary_risk_state")

    if hard_failures:
        disposition = "decline"
    elif missing_fields:
        disposition = str(appetite.get("on_missing_required_data", "insufficient_information"))
    elif referral_rules:
        disposition = "refer"
        for referral in referral_rules:
            add_rule(referral["rule_id"], "referral", referral["detail"], *referral["fields"])
    else:
        disposition = "accept"

    missing_information = [
        {"field": field, "evidence_path": f"policy.{field}", "reason": "This fact is required to apply the selected appetite profile."}
        for field in missing_fields
    ]
    recommended_conditions = [
        {"condition_type": "request_information", "field": item["field"], "reason": item["reason"]}
        for item in missing_information
    ]
    if disposition in {"accept", "refer"}:
        for condition in rules.get("conditions", []):
            recommended_conditions.append({"condition_type": "appetite_condition", "field": "", "reason": str(condition)})

    summaries = {
        "accept": "The supplied policy facts satisfy the selected appetite.",
        "refer": "The policy meets hard eligibility requirements but requires specialist review.",
        "decline": "One or more hard appetite rules are not met.",
        "insufficient_information": "Required policy facts are missing for this appetite evaluation.",
    }
    return {
        "appetite_id": appetite_id,
        "appetite_version": str(appetite.get("appetite_version", appetite_id)),
        "disposition": disposition,
        "decision_summary": summaries.get(disposition, "The selected appetite requires additional handling."),
        "hard_failures": hard_failures,
        "target_matches": target_matches,
        "missing_fields": missing_fields,
        "missing_information": missing_information,
        "driving_rules": driving_rules,
        "evidence": evidence,
        "recommended_conditions": recommended_conditions,
    }


def model_target(evaluation: Mapping[str, Any]) -> dict[str, Any]:
    """Project an internal audit result into Qwen's exact two-field contract."""

    disposition = str(evaluation["disposition"])
    if disposition == "decline":
        source_rule_ids = [str(item["rule_id"]) for item in evaluation["hard_failures"]]
    elif disposition == "refer":
        source_rule_ids = [
            str(item["rule_id"])
            for item in evaluation["driving_rules"]
            if item.get("outcome") == "referral"
        ]
    elif disposition == "insufficient_information":
        source_rule_ids = [MISSING_FIELD_RULES.get(str(field), str(field)) for field in evaluation["missing_fields"]]
    else:
        source_rule_ids = [
            str(item["rule_id"])
            for item in evaluation["driving_rules"]
            if item.get("outcome") == "acceptable"
        ]

    expected_rules: list[str] = []
    for rule_id in source_rule_ids:
        normalized = MODEL_RULE_ALIASES.get(rule_id, rule_id)
        if normalized not in expected_rules:
            expected_rules.append(normalized)
    return {"expected_disposition": disposition, "expected_rules": expected_rules}
