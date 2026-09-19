from __future__ import annotations

from copy import deepcopy
from typing import Any

from .demo_data import DEMO_SUBMISSIONS


DEMO_SCHEMA: dict[str, Any] = {
    "Submission": {
        "type": "object",
        "fields": {
            "id": {"type": "string"},
            "submission_number": {"type": "string"},
            "received_date": {"type": "string"},
            "policy": {
                "type": "reference",
                "resource": "Policy",
                "cardinality": "one",
            },
            "insured": {
                "type": "reference",
                "resource": "Insured",
                "cardinality": "one",
            },
        },
    },
    "Policy": {
        "type": "object",
        "fields": {
            "id": {"type": "string"},
            "submission_type": {"type": "string"},
            "line_of_business": {"type": "string"},
            "premium": {"type": "number"},
            "tiv": {"type": "number"},
            "primary_state": {"type": "string"},
            "effective_date": {"type": "string"},
            "expiration_date": {"type": "string"},
            "buildings": {
                "type": "reference",
                "resource": "Building",
                "cardinality": "many",
            },
            "claims": {
                "type": "reference",
                "resource": "Claim",
                "cardinality": "many",
            },
        },
    },
    "Insured": {
        "type": "object",
        "fields": {
            "id": {"type": "string"},
            "name": {"type": "string"},
        },
    },
    "Building": {
        "type": "object",
        "fields": {
            "id": {"type": "string"},
            "policy_id": {"type": "string"},
            "year_built": {"type": "number"},
            "construction_type": {"type": "string"},
            "tiv": {"type": "number"},
            "occupancy": {"type": "string"},
            "sprinklered": {"type": "boolean"},
            "protection_class": {"type": "string"},
            "flood_zone": {"type": "string"},
            "wildfire_score": {"type": "number"},
        },
    },
    "Claim": {
        "type": "object",
        "fields": {
            "id": {"type": "string"},
            "policy_id": {"type": "string"},
            "loss_date": {"type": "string"},
            "loss_value": {"type": "number"},
        },
    },
    "Location": {
        "type": "object",
        "fields": {
            "id": {"type": "string"},
            "state": {"type": "string"},
        },
    },
    "ExposureUnit": {
        "type": "object",
        "fields": {
            "id": {"type": "string"},
            "kind": {"type": "string"},
            "basis_amount": {"type": "number"},
        },
    },
}


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def demo_records() -> dict[str, list[dict[str, Any]]]:
    records: dict[str, list[dict[str, Any]]] = {
        resource: [] for resource in DEMO_SCHEMA
    }
    for submission in DEMO_SUBMISSIONS:
        building_ids = [item.id for item in submission.buildings]
        claim_ids = [item.id for item in submission.claims]
        records["Submission"].append(
            {
                "id": submission.id,
                "submission_number": submission.submission_number,
                "received_date": _iso(submission.received_date),
                "policy": submission.id,
                "insured": submission.id,
            }
        )
        records["Policy"].append(
            {
                "id": submission.id,
                "submission_type": submission.submission_type,
                "line_of_business": submission.line_of_business,
                "premium": submission.premium,
                "tiv": submission.tiv,
                "primary_state": submission.primary_state,
                "effective_date": _iso(submission.effective_date),
                "expiration_date": _iso(submission.expiration_date),
                "buildings": building_ids,
                "claims": claim_ids,
            }
        )
        records["Insured"].append(
            {"id": submission.id, "name": submission.insured_name}
        )
        records["Location"].append(
            {"id": submission.id, "state": submission.primary_state}
        )
        for building in submission.buildings:
            row = building.model_dump(mode="json")
            row["policy_id"] = submission.id
            records["Building"].append(row)
        for claim in submission.claims:
            row = claim.model_dump(mode="json")
            row["policy_id"] = submission.id
            records["Claim"].append(row)
    return records


def _get(record: dict[str, Any], path: str) -> Any:
    def descend(current: Any, segments: list[str]) -> Any:
        if not segments:
            return current
        if isinstance(current, list):
            return [descend(item, segments) for item in current]
        if not isinstance(current, dict):
            return None
        return descend(current.get(segments[0]), segments[1:])

    return descend(record, path.split("."))


def _set(record: dict[str, Any], path: str, value: Any) -> None:
    segments = path.split(".")
    current = record
    for segment in segments[:-1]:
        next_value = current.get(segment)
        if not isinstance(next_value, dict):
            next_value = {}
            current[segment] = next_value
        current = next_value
    current[segments[-1]] = value


def _compare(actual: Any, operator: str, expected: Any) -> bool:
    if operator == "$eq":
        return actual == expected
    if operator == "$ne":
        return actual != expected
    if operator == "$exists":
        return (actual is not None) is bool(expected)
    if operator == "$in":
        if isinstance(actual, list):
            return any(item in expected for item in actual)
        return actual in expected
    if operator == "$nin":
        return not _compare(actual, "$in", expected)
    if operator == "$contains":
        return expected in actual if actual is not None else False
    if operator == "$gt":
        return actual is not None and actual > expected
    if operator == "$gte":
        return actual is not None and actual >= expected
    if operator == "$lt":
        return actual is not None and actual < expected
    if operator == "$lte":
        return actual is not None and actual <= expected
    return False


def _matches(record: dict[str, Any], clause: Any) -> bool:
    if not isinstance(clause, dict):
        return True
    for key, expected in clause.items():
        if key == "$and":
            if not all(_matches(record, item) for item in expected):
                return False
            continue
        if key == "$or":
            if not any(_matches(record, item) for item in expected):
                return False
            continue
        if key == "$not":
            if _matches(record, expected):
                return False
            continue
        actual = _get(record, key)
        if isinstance(expected, dict):
            if "$elemMatch" in expected:
                values = actual if isinstance(actual, list) else []
                if not any(_matches(item, expected["$elemMatch"]) for item in values if isinstance(item, dict)):
                    return False
            elif not all(_compare(actual, operator, value) for operator, value in expected.items()):
                return False
        elif actual != expected:
            return False
    return True


def _project(record: dict[str, Any], select: Any) -> dict[str, Any]:
    if not select:
        return record
    if isinstance(select, list):
        projected: dict[str, Any] = {}
        for item in select:
            if isinstance(item, str):
                _set(projected, item, _get(record, item))
            elif isinstance(item, dict):
                projected.update(_project(record, item))
        return projected
    if isinstance(select, dict):
        projected = {}
        for name, directive in select.items():
            if directive is True:
                projected[name] = record.get(name)
            elif isinstance(directive, dict) and not any(
                str(key).startswith("$") for key in directive
            ):
                nested = record.get(name)
                if isinstance(nested, list):
                    projected[name] = [
                        _project(item, directive) if isinstance(item, dict) else item
                        for item in nested
                    ]
                elif isinstance(nested, dict):
                    projected[name] = _project(nested, directive)
        return projected
    return record


def _reference(resource: str, field: str) -> dict[str, Any] | None:
    definition = DEMO_SCHEMA.get(resource, {}).get("fields", {}).get(field)
    return definition if isinstance(definition, dict) and definition.get("type") == "reference" else None


def _nested_expand(spec: Any) -> dict[str, Any]:
    if spec is True or spec == {}:
        return {}
    if isinstance(spec, str):
        return {spec: True}
    return spec if isinstance(spec, dict) else {}


def _expand_record(
    record: dict[str, Any],
    resource: str,
    expand: dict[str, Any],
    all_records: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    hydrated = deepcopy(record)
    for field, spec in expand.items():
        reference = _reference(resource, field)
        if reference is None:
            continue
        target = str(reference["resource"])
        target_by_id = {str(item.get("id")): item for item in all_records.get(target, [])}
        raw = hydrated.get(field)
        identifiers = raw if isinstance(raw, list) else [raw]
        nested = _nested_expand(spec)
        resolved = []
        for identifier in identifiers:
            identifier = identifier.get("id") if isinstance(identifier, dict) else identifier
            found = target_by_id.get(str(identifier))
            if found is not None:
                resolved.append(
                    _expand_record(found, target, nested, all_records)
                    if nested
                    else deepcopy(found)
                )
        hydrated[field] = (
            resolved
            if reference.get("cardinality") == "many"
            else (resolved[0] if resolved else None)
        )
    return hydrated


def _unwind(rows: list[dict[str, Any]], item: Any) -> list[dict[str, Any]]:
    path = item.get("path") if isinstance(item, dict) else item
    unwind_type = item.get("type", "inner") if isinstance(item, dict) else "inner"
    if not isinstance(path, str):
        return rows
    unwound: list[dict[str, Any]] = []
    for row in rows:
        value = _get(row, path)
        values = value if isinstance(value, list) else ([] if value is None else [value])
        if not values and unwind_type == "left":
            copy = deepcopy(row)
            _set(copy, path, None)
            unwound.append(copy)
        for value in values:
            copy = deepcopy(row)
            _set(copy, path, value)
            unwound.append(copy)
    return unwound


def _has_reduction(select: Any) -> bool:
    if isinstance(select, list):
        return any(_has_reduction(item) for item in select)
    if not isinstance(select, dict):
        return False
    return any(
        str(key) in {"$sum", "$avg", "$min", "$max", "$count", "$countDistinct"}
        or _has_reduction(value)
        for key, value in select.items()
    )


def _flatten(values: list[Any]) -> list[Any]:
    flattened: list[Any] = []
    for value in values:
        if isinstance(value, list):
            flattened.extend(_flatten(value))
        elif value is not None:
            flattened.append(value)
    return flattened


def _reduce(rows: list[dict[str, Any]], directive: dict[str, Any]) -> Any:
    operator, operand = next(iter(directive.items()))
    if operator == "$count":
        return len(rows)
    paths = operand if isinstance(operand, list) else [operand]
    values = _flatten(
        [_get(row, str(path)) for row in rows for path in paths]
    )
    if operator == "$countDistinct":
        return len({str(value) for value in values})
    if not values:
        return None
    if operator == "$sum":
        return sum(values)
    if operator == "$avg":
        return sum(values) / len(values)
    if operator == "$min":
        return min(values)
    if operator == "$max":
        return max(values)
    return None


def _project_group(rows: list[dict[str, Any]], select: dict[str, Any]) -> dict[str, Any]:
    projected: dict[str, Any] = {}
    first = rows[0]
    for name, directive in select.items():
        if directive is True:
            projected[name] = first.get(name)
        elif isinstance(directive, dict) and len(directive) == 1 and next(iter(directive)).startswith("$"):
            projected[name] = _reduce(rows, directive)
        elif isinstance(directive, dict):
            projected[name] = _project_group(rows, directive)
    return projected


def query_demo(query: dict[str, Any]) -> dict[str, Any]:
    all_records = demo_records()
    resource = str(query["resource"])
    rows = [item for item in all_records.get(resource, []) if _matches(item, query.get("where", {}))]
    expand = query.get("expand")
    if isinstance(expand, dict):
        rows = [_expand_record(item, resource, expand, all_records) for item in rows]
    for item in query.get("unwind", []):
        rows = _unwind(rows, item)
    rows = [item for item in rows if _matches(item, query.get("filter", {}))]

    select = query.get("select")
    over = query.get("over")
    grouped = bool(over) or _has_reduction(select)
    if grouped:
        paths = over if isinstance(over, list) and over else ["id"]
        groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
        for row in rows:
            key = tuple(str(_get(row, str(path))) for path in paths)
            groups.setdefault(key, []).append(row)
        rows = [
            _project_group(group_rows, select if isinstance(select, dict) else {})
            for group_rows in groups.values()
        ]
    else:
        rows = [_project(item, select) for item in rows]
    for sort in reversed(query.get("sort", [])):
        field = sort.get("field")
        rows.sort(
            key=lambda item: (_get(item, field) is None, _get(item, field)),
            reverse=sort.get("direction") == "desc",
        )
    total = len(rows)
    pagination = query.get("pagination", {})
    offset = pagination.get("offset", 0)
    limit = pagination.get("limit", 25)
    rows = rows[offset : offset + limit]
    result_key = "groups" if grouped else "records"
    return {"resource": resource, "total": total, result_key: rows}
