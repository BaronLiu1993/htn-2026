"""Fetch expanded Federato policies and normalize them into benchmark schema v1."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any

from federato_api import FederatoClient
from federato_auth import FederatoAuth

from training_data.appetite import BENCHMARK_SCHEMA_VERSION


BENCHMARK_DIRECTORY = Path(__file__).with_name("benchmark")
DEFAULT_LIVE_DIRECTORY = Path("training_data/live_data")
COMMON_FIELD_NAMES = {
    "premium": ("premium", "written_premium", "total_premium"),
    "business_type": ("business_type", "submission_type", "transaction_type"),
    "primary_risk_state": ("primary_risk_state", "primary_state"),
    "total_tiv": ("total_tiv", "tiv", "total_insured_value"),
    "loss_value_5yr": ("loss_value_5yr", "five_year_loss_value", "five_year_losses"),
    "location_count": ("location_count",),
}


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _path_value(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


def _first_value(record: Mapping[str, Any], paths: Iterable[str]) -> Any:
    for path in paths:
        value = _path_value(record, path)
        if value not in (None, ""):
            return value
    return None


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _number(value: Any) -> float | int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(str(value).replace(",", "").replace("$", "").strip())
    except ValueError:
        return None


def _date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None


def _five_year_window(policy: Mapping[str, Any]) -> tuple[date, date] | None:
    dates = policy.get("dates")
    if not isinstance(dates, Mapping):
        return None
    as_of = _date(dates.get("effective")) or _date(dates.get("expiration"))
    if as_of is None:
        return None
    try:
        return as_of.replace(year=as_of.year - 5), as_of
    except ValueError:
        return as_of.replace(year=as_of.year - 5, day=28), as_of


def _expanded_locations(policy: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    locations: list[Mapping[str, Any]] = []
    for unit in _as_list(policy.get("exposure_units")):
        if not isinstance(unit, Mapping):
            continue
        location = unit.get("location")
        if isinstance(location, Mapping):
            locations.append(location)
    return locations


def _derive_facts(policy: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Normalize common schema variants while retaining an explicit warning trail."""

    locations = _expanded_locations(policy)
    claims = [claim for claim in _as_list(policy.get("claims")) if isinstance(claim, Mapping)]
    warnings: list[str] = []
    facts = {field: _first_value(policy, paths) for field, paths in COMMON_FIELD_NAMES.items()}
    states = sorted(
        {
            str(state).upper()
            for location in locations
            for state in [_first_value(location, ("state", "address.state"))]
            if state not in (None, "")
        }
    )
    facts["states"] = states or None
    facts["location_count"] = facts["location_count"] or (len(locations) if locations else None)
    if facts["primary_risk_state"] is None:
        weighted_states: list[tuple[float | int, str]] = []
        for unit in _as_list(policy.get("exposure_units")):
            if not isinstance(unit, Mapping) or not isinstance(unit.get("location"), Mapping):
                continue
            state = _first_value(unit["location"], ("state", "address.state"))
            basis = _number(_first_value(unit, ("basis_amount", "tiv", "total_insured_value")))
            if state not in (None, "") and basis is not None:
                weighted_states.append((basis, str(state).upper()))
        if weighted_states:
            facts["primary_risk_state"] = max(weighted_states, key=lambda item: item[0])[1]

    building_years = [
        _number(_first_value(building, ("year_built", "building_year", "year")))
        for location in locations
        for building in _as_list(location.get("buildings"))
        if isinstance(building, Mapping)
    ]
    known_building_years = [year for year in building_years if year is not None]
    facts["min_building_year"] = min(known_building_years) if known_building_years else None

    if facts["total_tiv"] is None:
        tiv_values = [
            _number(_first_value(unit, ("basis_amount", "tiv", "total_insured_value")))
            for unit in _as_list(policy.get("exposure_units"))
            if isinstance(unit, Mapping)
        ]
        numbers = [value for value in tiv_values if value is not None]
        facts["total_tiv"] = sum(numbers) if numbers else None

    if facts["loss_value_5yr"] is None and isinstance(policy.get("claims"), list):
        window = _five_year_window(policy)
        claim_totals: list[float | int] = []
        dated_claims = [(_date(claim.get("date_of_loss")), claim) for claim in claims]
        if claims and (window is None or any(claim_date is None for claim_date, _ in dated_claims)):
            facts["loss_value_5yr"] = None
            warnings.append("unusable:loss_value_5yr_date_window")
        else:
            for claim_date, claim in dated_claims:
                if window is not None and not (window[0] <= claim_date <= window[1]):
                    continue
                components = [
                    _number(_first_value(claim, ("paid_indemnity",))),
                    _number(_first_value(claim, ("paid_expense",))),
                    _number(_first_value(claim, ("reserve_indemnity",))),
                    _number(_first_value(claim, ("reserve_expense",))),
                ]
                values = [component for component in components if component is not None]
                if values:
                    claim_totals.append(sum(values))
            facts["loss_value_5yr"] = sum(claim_totals)

    hazards = sorted(
        {
            str(hazard).strip().lower()
            for source in [*locations, policy]
            for hazard in _as_list(_first_value(source, ("hazard_tags", "hazards")))
            if str(hazard).strip()
        }
    )
    facts["hazards"] = hazards if locations else None
    statuses = [str(_first_value(claim, ("status", "claim_status")) or "").strip().lower() for claim in claims]
    claims_are_expanded = isinstance(policy.get("claims"), list)
    facts["open_claim_count"] = sum(status in {"open", "active", "pending"} for status in statuses) if claims_are_expanded else None
    facts["litigated_claim_count"] = sum(
        bool(_first_value(claim, ("litigated", "is_litigated", "in_litigation"))) for claim in claims
    ) if claims_are_expanded else None

    for field, value in facts.items():
        if value is None:
            warnings.append(f"missing:{field}")
    return facts, warnings


def _records_from_response(response: Any) -> tuple[list[dict[str, Any]], int | None]:
    if isinstance(response, list):
        return [item for item in response if isinstance(item, dict)], None
    if not isinstance(response, Mapping):
        raise ValueError("Federato query response was not an object or array")
    for key in ("output", "records", "results", "data", "items", "groups"):
        value = response.get(key)
        if isinstance(value, list):
            if key == "output" and len(value) == 1 and isinstance(value[0], Mapping):
                nested = value[0].get("data")
                if isinstance(nested, Mapping) and isinstance(nested.get("results"), list):
                    total = _number(nested.get("total"))
                    return [item for item in nested["results"] if isinstance(item, dict)], int(total) if total is not None else None
            total = _number(response.get("total"))
            return [item for item in value if isinstance(item, dict)], int(total) if total is not None else None
    raise ValueError("Federato query response did not contain a recognized records array")


async def _fetch_policies(client: FederatoClient, query: dict[str, Any], limit: int, page_size: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    schema = await client.fetch_schema()
    records: list[dict[str, Any]] = []
    offset = 0
    while len(records) < limit:
        request = {**query, "pagination": {"limit": min(page_size, limit - len(records)), "offset": offset}}
        response = await client.query(request)
        page, total = _records_from_response(response)
        records.extend(page)
        if not page or len(page) < request["pagination"]["limit"] or (total is not None and len(records) >= total):
            break
        offset += len(page)
    return records[:limit], schema


def _normalized_policy(raw: Mapping[str, Any]) -> dict[str, Any]:
    raw_id = _first_value(raw, ("id", "policy_id"))
    if raw_id is None:
        raise ValueError("Federato policy record did not contain an id")
    facts, warnings = _derive_facts(raw)
    policy_number = _first_value(raw, ("policy_number", "number"))
    return {
        "policy_id": f"federato-policy-{raw_id}",
        "kind": "live",
        "source": {"resource": "Policy", "id": raw_id, "policy_number": policy_number},
        "facts": facts,
        "normalization_warnings": warnings,
    }


async def collect(
    query_path: Path,
    base_policies_path: Path,
    normalized_output: Path,
    raw_output: Path,
    limit: int,
    page_size: int,
) -> dict[str, Any]:
    query_document = _read_json(query_path)
    query_envelope = query_document.get("live_query", query_document)
    query = query_envelope.get("payload", query_envelope) if isinstance(query_envelope, dict) else None
    if not isinstance(query, dict) or query.get("resource") != "Policy":
        raise ValueError("Live query must contain a Policy resource payload")
    base = _read_json(base_policies_path)
    if base.get("schema_version") != BENCHMARK_SCHEMA_VERSION or not isinstance(base.get("policies"), list):
        raise ValueError("Base policies file does not use benchmark schema v1")

    auth = FederatoAuth.from_environment()
    client = FederatoClient(auth)
    try:
        raw_records, schema = await _fetch_policies(client, query, limit, page_size)
    finally:
        await client.aclose()
        await auth.aclose()

    return _write_normalized_dataset(base, raw_records, schema, normalized_output, raw_output)


def _write_normalized_dataset(
    base: Mapping[str, Any],
    raw_records: list[dict[str, Any]],
    schema: Any,
    normalized_output: Path,
    raw_output: Path,
) -> dict[str, Any]:
    normalized = [_normalized_policy(record) for record in raw_records]
    existing_ids = {str(policy.get("policy_id")) for policy in base["policies"]}
    new_policies = [policy for policy in normalized if policy["policy_id"] not in existing_ids]
    normalized_output.parent.mkdir(parents=True, exist_ok=True)
    raw_output.parent.mkdir(parents=True, exist_ok=True)
    raw_output.write_text(json.dumps({"schema": schema, "records": raw_records}, indent=2, sort_keys=True) + "\n")
    merged = {**base, "policies": [*base["policies"], *new_policies]}
    normalized_output.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n")
    return {
        "raw_records_fetched": len(raw_records),
        "normalized_live_policies_added": len(new_policies),
        "normalized_output": str(normalized_output),
        "raw_output": str(raw_output),
    }


def rebuild_from_raw(raw_snapshot: Path, base_policies_path: Path, normalized_output: Path) -> dict[str, Any]:
    """Re-run normalization after mapping logic changes without another API call."""

    snapshot = _read_json(raw_snapshot)
    records = snapshot.get("records")
    if not isinstance(records, list) or not all(isinstance(record, dict) for record in records):
        raise ValueError("Raw snapshot must contain a records array")
    base = _read_json(base_policies_path)
    if base.get("schema_version") != BENCHMARK_SCHEMA_VERSION or not isinstance(base.get("policies"), list):
        raise ValueError("Base policies file does not use benchmark schema v1")
    temporary_raw_output = raw_snapshot.with_suffix(".rebuild.json")
    result = _write_normalized_dataset(base, records, snapshot.get("schema"), normalized_output, temporary_raw_output)
    temporary_raw_output.unlink(missing_ok=True)
    result["raw_output"] = str(raw_snapshot)
    result["rebuild_from_raw"] = True
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", type=Path, default=BENCHMARK_DIRECTORY / "policies.json")
    parser.add_argument("--base-policies", type=Path, default=BENCHMARK_DIRECTORY / "policies.json")
    parser.add_argument("--normalized-output", type=Path, default=DEFAULT_LIVE_DIRECTORY / "expanded-policies.json")
    parser.add_argument("--raw-output", type=Path, default=DEFAULT_LIVE_DIRECTORY / "raw-policy-snapshot.json")
    parser.add_argument("--rebuild-from-raw", type=Path, help="Re-normalize an existing ignored raw snapshot without an API call")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--page-size", type=int, default=100)
    args = parser.parse_args()
    if args.limit <= 0 or args.page_size <= 0:
        raise ValueError("limit and page-size must be positive")
    if args.rebuild_from_raw:
        result = rebuild_from_raw(args.rebuild_from_raw, args.base_policies, args.normalized_output)
    else:
        result = asyncio.run(collect(args.query, args.base_policies, args.normalized_output, args.raw_output, args.limit, args.page_size))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
