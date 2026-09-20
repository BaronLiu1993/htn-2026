"""Create profile-aware, leakage-safe underwriting counterfactual policies."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from training_data.appetite import BENCHMARK_SCHEMA_VERSION


BENCHMARK_DIRECTORY = Path(__file__).with_name("benchmark")
CORE_FACTS = ("premium", "business_type", "states", "total_tiv", "min_building_year", "loss_value_5yr", "hazards")


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _number(value: Any) -> float | int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(str(value).replace(",", "").replace("$", "").strip())
    except ValueError:
        return None


def _stable_order(items: list[dict[str, Any]], seed: str) -> list[dict[str, Any]]:
    return sorted(items, key=lambda item: hashlib.sha256(f"{seed}:{item['name']}".encode()).hexdigest())


def _variants_for_appetite(appetite: Mapping[str, Any]) -> list[dict[str, Any]]:
    rules = appetite["rules"]
    variants: list[dict[str, Any]] = [{"name": "profile_clean_accept", "changes": {}}]

    premium = rules.get("premium")
    if isinstance(premium, Mapping):
        minimum, maximum = _number(premium.get("min")), _number(premium.get("max"))
        if minimum is not None:
            variants.extend(
                [
                    {"name": "premium_at_min", "changes": {"premium": minimum}},
                    {"name": "premium_below_min", "changes": {"premium": minimum - 1}},
                ]
            )
        if maximum is not None:
            variants.extend(
                [
                    {"name": "premium_at_max", "changes": {"premium": maximum}},
                    {"name": "premium_above_max", "changes": {"premium": maximum + 1}},
                ]
            )

    tiv_max = _number(rules.get("total_tiv_max"))
    if tiv_max is not None:
        variants.extend(
            [
                {"name": "tiv_at_max", "changes": {"total_tiv": tiv_max}},
                {"name": "tiv_above_max", "changes": {"total_tiv": tiv_max + 1}},
            ]
        )

    year_min = _number(rules.get("building_year_exclusive_min"))
    if year_min is not None:
        variants.extend(
            [
                {"name": "building_year_at_exclusive_min", "changes": {"min_building_year": year_min}},
                {"name": "building_year_just_above_min", "changes": {"min_building_year": year_min + 1}},
            ]
        )

    loss_max = _number(rules.get("loss_value_5yr_max"))
    if loss_max is not None:
        variants.extend(
            [
                {"name": "loss_at_max", "changes": {"loss_value_5yr": loss_max}},
                {"name": "loss_above_max", "changes": {"loss_value_5yr": loss_max + 1}},
            ]
        )

    for field in CORE_FACTS:
        variants.append({"name": f"missing_{field}", "changes": {field: None}})

    for rule_name in ("prohibited_hazards", "refer_hazards"):
        hazards = rules.get(rule_name)
        if isinstance(hazards, list) and hazards:
            variants.append({"name": rule_name, "changes": {"hazards": [str(hazards[0])]}})

    if rules.get("refer_if_multistate") or rules.get("require_primary_risk_location_for_multistate"):
        variants.append({"name": "multistate_without_primary", "changes": {"states": ["CA", "NY"], "primary_risk_state": None}})
    if "location_count_max" in rules:
        maximum = _number(rules["location_count_max"])
        if maximum is not None:
            variants.append({"name": "location_count_above_max", "changes": {"location_count": maximum + 1}})
    if rules.get("open_or_litigated_claims_allowed") is False:
        variants.append({"name": "open_claim", "changes": {"open_claim_count": 1, "litigated_claim_count": 0}})
        variants.append({"name": "litigated_claim", "changes": {"open_claim_count": 0, "litigated_claim_count": 1}})
    return variants


def _profile_compliant_anchor(appetite: Mapping[str, Any]) -> dict[str, Any]:
    """Build a clean profile baseline so each synthetic mutation isolates one rule."""

    rules = appetite["rules"]
    allowed_states = rules.get("eligible_states")
    state = str(allowed_states[0]) if isinstance(allowed_states, list) and allowed_states else "CA"
    premium_rule = rules.get("premium") if isinstance(rules.get("premium"), Mapping) else {}
    premium_min = _number(premium_rule.get("min")) or 50_000
    premium_max = _number(premium_rule.get("max")) or premium_min
    tiv_max = _number(rules.get("total_tiv_max")) or 25_000_000
    year_min = _number(rules.get("building_year_exclusive_min")) or 1990
    loss_max = _number(rules.get("loss_value_5yr_max")) or 0
    business_types = rules.get("business_types")
    business_type = str(business_types[0]) if isinstance(business_types, list) and business_types else "new"
    return {
        "premium": (premium_min + premium_max) / 2,
        "business_type": business_type,
        "states": [state],
        "primary_risk_state": state,
        "total_tiv": tiv_max / 2,
        "min_building_year": year_min + 10,
        "loss_value_5yr": min(loss_max, 10_000),
        "location_count": 1,
        "hazards": [],
        "open_claim_count": 0,
        "litigated_claim_count": 0,
    }


def enrich(policies_path: Path, appetites_path: Path, output_path: Path, per_appetite: int) -> dict[str, Any]:
    policies_document = _read_json(policies_path)
    appetites_document = _read_json(appetites_path)
    if policies_document.get("schema_version") != BENCHMARK_SCHEMA_VERSION:
        raise ValueError("Policies file does not use benchmark schema v1")
    if appetites_document.get("schema_version") != BENCHMARK_SCHEMA_VERSION:
        raise ValueError("Appetites file does not use benchmark schema v1")
    policies = policies_document.get("policies")
    appetites = appetites_document.get("appetites")
    if not isinstance(policies, list) or not isinstance(appetites, list):
        raise ValueError("Benchmark files require policies and appetites arrays")

    existing_ids = {str(policy.get("policy_id")) for policy in policies if isinstance(policy, dict)}
    live_policies = [policy for policy in policies if isinstance(policy, dict) and policy.get("kind") == "live"]
    source_policies = [
        policy
        for policy in live_policies
        if isinstance(policy.get("facts"), dict) and all(policy["facts"].get(field) not in (None, "") for field in CORE_FACTS)
    ]
    generated: list[dict[str, Any]] = []
    for policy in source_policies:
        policy_id = str(policy.get("policy_id"))
        facts = policy.get("facts")
        if not policy_id or not isinstance(facts, dict):
            continue
        for appetite in appetites:
            if not isinstance(appetite, dict) or not isinstance(appetite.get("rules"), dict):
                continue
            appetite_id = str(appetite.get("appetite_id", "appetite"))
            variants = _variants_for_appetite(appetite)
            clean_anchor = next(variant for variant in variants if variant["name"] == "profile_clean_accept")
            selected = [clean_anchor]
            if per_appetite > 1:
                selected.extend(
                    _stable_order(
                        [variant for variant in variants if variant["name"] != "profile_clean_accept"],
                        f"{policy_id}:{appetite_id}",
                    )[: per_appetite - 1]
                )
            for variant in selected:
                variant_id = f"synthetic-{policy_id}-{appetite_id}-{variant['name']}"
                if variant_id in existing_ids:
                    continue
                variant_facts = {**facts, **_profile_compliant_anchor(appetite), **variant["changes"]}
                generated.append(
                    {
                        "policy_id": variant_id,
                        "kind": "synthetic",
                        "scenario": f"{appetite_id}:{variant['name']}",
                        "counterfactual_of": policy_id,
                        "facts": variant_facts,
                    }
                )
                existing_ids.add(variant_id)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps({**policies_document, "policies": [*policies, *generated]}, indent=2, sort_keys=True) + "\n")
    return {
        "source_live_policies": len(source_policies),
        "live_policies_excluded_for_missing_core_facts": len(live_policies) - len(source_policies),
        "synthetic_policies_added": len(generated),
        "output": str(output_path),
        "per_appetite": per_appetite,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policies", type=Path, default=Path("training_data/live_data/expanded-policies.json"))
    parser.add_argument("--appetites", type=Path, default=BENCHMARK_DIRECTORY / "appetites.json")
    parser.add_argument("--output", type=Path, default=Path("training_data/live_data/enriched-policies.json"))
    parser.add_argument("--per-appetite", type=int, default=4, help="Deterministic variants per source policy and appetite")
    args = parser.parse_args()
    if args.per_appetite <= 0:
        raise ValueError("per-appetite must be positive")
    print(json.dumps(enrich(args.policies, args.appetites, args.output, args.per_appetite), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
