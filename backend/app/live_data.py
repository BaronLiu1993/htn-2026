from __future__ import annotations

import asyncio
import re
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable

from .federato_client import FederatoError
from .guideline_registry import GuidelinePackage
from .models import BuildingEvidence, ClaimEvidence, SubmissionEvidence, TraceEvent
from .rule_engine import matches
from .schema_registry import SchemaRegistry


TraceSink = Callable[[TraceEvent], None]


def _rows(payload: Any) -> tuple[list[dict[str, Any]], int | None]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)], len(payload)
    if not isinstance(payload, dict):
        return [], None
    for key in ("records", "results", "items", "data", "groups"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)], payload.get("total")
        if isinstance(value, dict):
            nested, nested_total = _rows(value)
            if nested:
                return nested, nested_total
    if "id" in payload or "_id" in payload:
        return [payload], 1
    return [], payload.get("total") if isinstance(payload.get("total"), int) else None


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    output: dict[str, Any] = {}
    if not isinstance(value, dict):
        return output
    for name, child in value.items():
        path = f"{prefix}.{name}" if prefix else str(name)
        output[path] = child
        if isinstance(child, dict):
            output.update(_flatten(child, path))
    return output


def _first(record: dict[str, Any] | None, aliases: tuple[str, ...]) -> Any:
    if not record:
        return None
    flattened = _flatten(record)
    normalized = {_key(path.split(".")[-1]): value for path, value in flattened.items()}
    normalized_paths = {_key(path): value for path, value in flattened.items()}
    for alias in aliases:
        if _key(alias) in normalized_paths:
            return normalized_paths[_key(alias)]
        if _key(alias) in normalized:
            return normalized[_key(alias)]
    return None


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = re.sub(r"[^0-9.\-]", "", value)
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _text(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    return str(value)


def _boolean(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "y", "1", "sprinklered"}:
            return True
        if normalized in {"false", "no", "n", "0", "not sprinklered"}:
            return False
    return None


def _date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            try:
                return date.fromisoformat(value[:10])
            except ValueError:
                return None
    return None


def _identifier(record: dict[str, Any]) -> str | None:
    return SchemaRegistry.canonical_identifier(record.get("id") or record.get("_id"))


def _ids_match(left: Any, right: Any) -> bool:
    first = SchemaRegistry.canonical_identifier(left)
    second = SchemaRegistry.canonical_identifier(right)
    return first is not None and first == second


def _ref_ids(value: Any) -> list[str]:
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    identifiers: list[str] = []
    for item in items:
        identifier = (
            _identifier(item)
            if isinstance(item, dict)
            else SchemaRegistry.canonical_identifier(item)
        )
        if identifier:
            identifiers.append(identifier)
    return identifiers


def _unique_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for row in rows:
        identifier = _identifier(row)
        if identifier is None:
            output.append(row)
            continue
        if identifier in seen:
            continue
        seen.add(identifier)
        output.append(row)
    return output


def _headquarters_location(
    registry: SchemaRegistry,
    insured: dict[str, Any] | None,
    insured_resource: str | None,
    location_resource: str | None,
    graph: dict[str, list[dict[str, Any]]],
) -> dict[str, Any] | None:
    if not insured or not insured_resource or not location_resource:
        return None
    hq_field = next(
        (
            reference.field
            for reference in registry.references_for(insured_resource)
            if reference.target == location_resource
            and _key(reference.field) in {"hq", "headquarters"}
        ),
        None,
    )
    if not hq_field:
        return None
    nested = insured.get(hq_field)
    if isinstance(nested, dict) and _identifier(nested):
        return nested
    hq_ids = set(_ref_ids(nested))
    for row in graph.get(location_resource, []):
        if _identifier(row) in hq_ids:
            return row
    return None


def _buildings_on_location(
    location: dict[str, Any] | None,
    building_resource: str | None,
    graph: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    if not location or not building_resource:
        return []
    nested = location.get("buildings")
    nested_rows = [
        item
        for item in (nested if isinstance(nested, list) else [nested])
        if isinstance(item, dict)
    ]
    ids = set(_ref_ids(nested))
    from_graph = [
        row
        for row in graph.get(building_resource, [])
        if _identifier(row) in ids
    ]
    return _unique_records([*nested_rows, *from_graph])


@dataclass
class ScopeSelection:
    resource: str
    identifier_field: str
    records: list[dict[str, Any]]
    in_scope_ids: list[str]
    outside_scope_ids: list[str]
    unknown_scope_ids: list[str]
    duplicate_ids: list[str]
    reported_total: int | None

    @property
    def available_count(self) -> int:
        return len({identifier for row in self.records if (identifier := _identifier(row))})


class LiveFederatoLoader:
    """Loads only package-declared resources and follows discovered references."""

    FIELD_ALIASES = {
        "Submission": (
            "submission_number",
            "number",
            "reference_number",
            "received_date",
            "submission_date",
            "created_at",
            "insured_name",
            "line_of_business",
        ),
        "Insured": (
            "name",
            "account_name",
            "insured_name",
            "legal_name",
        ),
        "Policy": (
            "business_type",
            "premium",
            "total_premium",
            "written_premium",
            "line_of_business",
            "submission",
        ),
        "Building": (
            "tiv",
            "total_insured_value",
            "year_built",
            "construction_year",
            "construction_type",
            "construction",
        ),
        "Claim": (
            "loss_date",
            "date_of_loss",
            "loss_value",
            "paid_indemnity",
            "paid_expense",
            "reserve_indemnity",
            "reserve_expense",
        ),
        "Location": (
            "state",
            "state_code",
            "primary_state",
            "buildings",
        ),
        "ExposureUnit": (
            "location",
        ),
    }

    def __init__(
        self,
        client: Any,
        registry: SchemaRegistry,
        trace_sink: TraceSink,
        semantic_resources: list[str] | None = None,
    ) -> None:
        self.client = client
        self.registry = registry
        self.trace_sink = trace_sink
        self.semantic_resources = semantic_resources or ["Submission"]
        self.records: dict[str, list[dict[str, Any]]] = {}
        self.scope_selection: ScopeSelection | None = None

    def _select_fields(
        self,
        resource: str,
        semantic: str,
        *,
        required_fields: tuple[str, ...] = (),
    ) -> list[str]:
        fields = self.registry.fields_for(resource)
        by_key = {_key(name): name for name in fields}
        wanted = {
            by_key[_key(alias)]
            for alias in self.FIELD_ALIASES.get(semantic, ())
            if _key(alias) in by_key
        }
        wanted.update(
            reference.field for reference in self.registry.references_for(resource)
        )
        for identifier in ("id", "_id"):
            if identifier in fields:
                wanted.add(identifier)
        wanted.update(field for field in required_fields if field in fields)
        return sorted(wanted)

    async def _query_all(
        self,
        resource: str,
        semantic: str,
        *,
        required_fields: tuple[str, ...] = (),
        where: dict[str, Any] | None = None,
        purpose: str | None = None,
    ) -> tuple[list[dict[str, Any]], int | None]:
        all_rows: list[dict[str, Any]] = []
        offset = 0
        limit = 100
        reported_total: int | None = None
        selected_fields = self._select_fields(
            resource,
            semantic,
            required_fields=required_fields,
        )
        while True:
            query = {
                "resource": resource,
                "pagination": {"limit": limit, "offset": offset},
            }
            if where:
                query["where"] = where
            if selected_fields:
                query["select"] = selected_fields
            payload = await self.client.query(
                query,
                purpose=purpose or f"Need {resource} evidence.",
            )
            page, total = _rows(payload)
            annotate = getattr(self.client, "annotate_query", None)
            if callable(annotate):
                annotate(
                    getattr(self.client, "last_query_audit_id", None),
                    {
                        "query_stage": "scope_selection",
                        "records_found": len(page),
                    },
                )
            if total is not None:
                reported_total = total
            all_rows.extend(page)
            if len(page) < limit or (total is not None and len(all_rows) >= total):
                break
            offset += limit
        return all_rows, reported_total

    async def load_insured_records(
        self,
        submission_resource: str,
        submissions: list[dict[str, Any]],
    ) -> None:
        insured_resource = self.registry.find_resource("Insured")
        if insured_resource is None:
            return
        direct_name_aliases = ("name", "account_name", "insured_name", "legal_name")
        insured_ids = {
            target_id
            for submission in submissions
            if _first(submission, direct_name_aliases) in (None, "")
            for target_resource, target_id in self.registry.iter_reference_values(
                submission_resource, submission
            )
            if target_resource == insured_resource
        }
        insured_ids -= {
            identifier
            for row in self.records.get(insured_resource, [])
            if (identifier := _identifier(row)) is not None
        }
        if not insured_ids:
            return
        identifier_field = self.registry.identifier_field(insured_resource)
        native_ids = [
            self.registry.coerce_identifier(insured_resource, identifier)
            for identifier in sorted(insured_ids)
        ]
        insured_rows, _ = await self._query_all(
            insured_resource,
            "Insured",
            where={identifier_field: {"$in": native_ids}},
            purpose="Need the insured name for the selected submissions.",
        )
        self.records.setdefault(insured_resource, []).extend(insured_rows)

    async def load_scope(
        self,
        package: GuidelinePackage,
        *,
        requested_ids: list[str] | None = None,
    ) -> ScopeSelection:
        resource = self.registry.find_resource(package.scope.source.resource)
        if resource is None:
            raise FederatoError(
                f'The discovered schema has no {package.scope.source.resource} scope resource.'
            )
        field = package.scope.source.field
        if not self.registry.field_exists(resource, field):
            raise FederatoError(
                f'The declared scope field "{field}" is unavailable on {resource}.'
            )
        identifier_field = self.registry.identifier_field(resource)
        rows, reported_total = await self._query_all(
            resource,
            "Submission",
            required_fields=(field,),
            purpose="Find submissions that match this guideline.",
        )
        requested = {
            identifier
            for value in (requested_ids or [])
            if (identifier := SchemaRegistry.canonical_identifier(value))
        }
        in_scope: list[str] = []
        outside_scope: list[str] = []
        unknown: list[str] = []
        seen: set[str] = set()
        duplicates: list[str] = []
        missing_identifiers = 0
        selected_rows: list[dict[str, Any]] = []
        for row in rows:
            identifier = _identifier(row)
            if identifier is None:
                missing_identifiers += 1
                continue
            if identifier in seen:
                duplicates.append(identifier)
                continue
            seen.add(identifier)
            if requested and identifier not in requested:
                continue
            selected_rows.append(row)
            value = _first(row, (field,))
            if value is None or value == "":
                unknown.append(identifier)
            elif matches(package.scope.operator, value, package.scope.value):
                in_scope.append(identifier)
            else:
                outside_scope.append(identifier)
        if missing_identifiers:
            raise FederatoError(
                f"Scope selection returned {missing_identifiers} record(s) without identifiers."
            )
        if reported_total is not None and len(rows) != reported_total:
            raise FederatoError(
                f"Scope pagination returned {len(rows)} of {reported_total} reported records."
            )
        self.scope_selection = ScopeSelection(
            resource=resource,
            identifier_field=identifier_field,
            records=selected_rows,
            in_scope_ids=in_scope,
            outside_scope_ids=outside_scope,
            unknown_scope_ids=unknown,
            duplicate_ids=duplicates,
            reported_total=reported_total,
        )
        candidate_rows: list[dict[str, Any]] = []
        if in_scope:
            candidate_set = set(in_scope)
            candidate_rows = [
                row
                for row in selected_rows
                if _identifier(row) in candidate_set
            ]
        self.records = {resource: candidate_rows}
        await self.load_insured_records(resource, candidate_rows)
        annotate = getattr(self.client, "annotate_query", None)
        if callable(annotate):
            annotate(
                getattr(self.client, "last_query_audit_id", None),
                {
                    "in_scope_records": len(in_scope),
                    "outside_scope_records": len(outside_scope),
                    "scope_unknown_records": len(unknown),
                },
            )
        return self.scope_selection

    async def load(self) -> list[SubmissionEvidence]:
        resource = self.registry.find_resource("Submission")
        if not resource:
            raise FederatoError("The discovered schema has no Submission resource.")
        rows, _ = await self._query_all(resource, "Submission")
        self.records = {resource: rows}
        await self.load_insured_records(resource, rows)
        return self.normalize(self.records)

    async def load_declared_resources(self) -> list[SubmissionEvidence]:
        """Load every resource named by the selected guideline's source plan."""

        pending: list[tuple[str, str]] = []
        for semantic in self.semantic_resources:
            resource = self.registry.find_resource(semantic)
            if resource and resource not in self.records:
                pending.append((resource, semantic))
        if pending:
            pages = await asyncio.gather(
                *(self._query_all(resource, semantic) for resource, semantic in pending)
            )
            self.records.update(
                {resource: rows for (resource, _), (rows, _) in zip(pending, pages)}
            )
        return self.normalize(self.records)

    def normalize(self, records: dict[str, list[dict[str, Any]]]) -> list[SubmissionEvidence]:
        resource_aliases = {
            semantic: actual for semantic in self.semantic_resources
            if (actual := self.registry.find_resource(semantic))
        }
        submission_resource = resource_aliases.get("Submission")
        if not submission_resource:
            raise FederatoError("The discovered schema has no Submission resource.")

        index: dict[tuple[str, str], dict[str, Any]] = {}
        adjacency: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
        expected_links: dict[tuple[str, str], dict[str, set[str]]] = defaultdict(
            lambda: defaultdict(set)
        )
        for resource, items in records.items():
            for item in items:
                identifier = _identifier(item)
                if identifier is not None:
                    index[(resource, identifier)] = item
        for (resource, identifier), item in index.items():
            node = (resource, identifier)
            for reference in self.registry.references_for(resource):
                if reference.field in item and item[reference.field] == []:
                    expected_links[node].setdefault(reference.target, set())
            for target_resource, target_id in self.registry.iter_reference_values(resource, item):
                target = (target_resource, target_id)
                expected_links[node][target_resource].add(target_id)
                if target in index:
                    adjacency[node].add(target)
                    adjacency[target].add(node)

        def related(
            start: tuple[str, str],
        ) -> tuple[dict[str, list[dict[str, Any]]], dict[str, set[str]]]:
            found: dict[str, list[dict[str, Any]]] = defaultdict(list)
            expected: dict[str, set[str]] = defaultdict(set)
            queue: deque[tuple[tuple[str, str], int]] = deque([(start, 0)])
            seen = {start}
            while queue:
                node, depth = queue.popleft()
                if (
                    node != start
                    and node[0] == submission_resource
                    and node[1] != start[1]
                ):
                    continue
                record = index.get(node)
                if record is not None:
                    found[node[0]].append(record)
                    for target_resource, identifiers in expected_links.get(node, {}).items():
                        expected[target_resource].update(identifiers)
                if depth >= 4:
                    continue
                for neighbor in adjacency.get(node, set()):
                    if (
                        neighbor[0] == submission_resource
                        and neighbor[1] != start[1]
                    ):
                        continue
                    if neighbor not in seen:
                        seen.add(neighbor)
                        queue.append((neighbor, depth + 1))
            return found, expected

        normalized: list[SubmissionEvidence] = []
        for item in records.get(submission_resource, []):
            submission_id = _identifier(item)
            if submission_id is None:
                continue
            graph, expected = related((submission_resource, submission_id))
            policy_resource = resource_aliases.get("Policy")
            exposure_resource = resource_aliases.get("ExposureUnit")
            location_resource = resource_aliases.get("Location")
            building_resource = resource_aliases.get("Building")
            claim_resource = resource_aliases.get("Claim")
            related_policies = graph.get(policy_resource, []) if policy_resource else []
            policies = [
                policy
                for policy in related_policies
                if not _ref_ids(policy.get("submission"))
                or any(
                    _ids_match(submission_id, item)
                    for item in _ref_ids(policy.get("submission"))
                )
            ]
            if policies:
                policy_ids = {identifier for policy in policies if (identifier := _identifier(policy))}
                claim_ids = {identifier for policy in policies for identifier in _ref_ids(policy.get("claims"))}
                exposure_ids = {
                    identifier
                    for policy in policies
                    for identifier in _ref_ids(policy.get("exposure_units"))
                }
                exposures = [
                    row
                    for row in (graph.get(exposure_resource, []) if exposure_resource else [])
                    if _identifier(row) in exposure_ids
                ]
                location_ids = {
                    identifier
                    for row in exposures
                    for identifier in _ref_ids(row.get("location"))
                } | {
                    identifier
                    for policy in policies
                    for identifier in _ref_ids(policy.get("locations"))
                }
                locations = [
                    row
                    for row in (graph.get(location_resource, []) if location_resource else [])
                    if _identifier(row) in location_ids
                ]
                building_ids = {
                    identifier
                    for row in locations
                    for identifier in _ref_ids(row.get("buildings"))
                } | {
                    identifier
                    for policy in policies
                    for identifier in _ref_ids(policy.get("buildings"))
                }
                buildings = [
                    row
                    for row in (graph.get(building_resource, []) if building_resource else [])
                    if _identifier(row) in building_ids
                ]
                claims = [
                    row
                    for row in (graph.get(claim_resource, []) if claim_resource else [])
                    if _identifier(row) in claim_ids
                ]
                if exposure_resource:
                    graph[exposure_resource] = exposures
                    expected[exposure_resource] = exposure_ids
                if policy_resource:
                    graph[policy_resource] = policies
                    expected[policy_resource] = policy_ids
                if claim_resource:
                    graph[claim_resource] = claims
                    expected[claim_resource] = claim_ids
            else:
                if policy_resource:
                    graph.pop(policy_resource, None)
                    expected.pop(policy_resource, None)
                if claim_resource:
                    graph.pop(claim_resource, None)
                    expected.pop(claim_resource, None)
                if exposure_resource:
                    graph.pop(exposure_resource, None)
                    expected.pop(exposure_resource, None)
                locations = []
                buildings = []
                claims = []
            policy = policies[0] if policies else None
            insured_resource = resource_aliases.get("Insured")
            insured = (graph.get(insured_resource, []) or [None])[0] if insured_resource else None
            hq_location = _headquarters_location(
                self.registry,
                insured,
                insured_resource,
                location_resource,
                graph,
            )
            hq_buildings = _buildings_on_location(
                hq_location,
                building_resource,
                graph,
            )
            if policies:
                if hq_location:
                    locations = _unique_records([*locations, hq_location])
                if hq_buildings:
                    buildings = _unique_records([*buildings, *hq_buildings])
            elif hq_location or hq_buildings:
                locations = [hq_location] if hq_location else locations
                buildings = hq_buildings or buildings
            else:
                locations = list(graph.get(location_resource, []) if location_resource else [])
                buildings = list(graph.get(building_resource, []) if building_resource else [])
            if location_resource:
                graph[location_resource] = locations
                expected[location_resource] = {
                    identifier
                    for row in locations
                    if (identifier := _identifier(row))
                }
            if building_resource:
                graph[building_resource] = buildings
                expected[building_resource] = {
                    identifier
                    for row in buildings
                    if (identifier := _identifier(row))
                }

            source = policy or item
            insured_name = _first(
                insured,
                ("name", "account_name", "insured_name", "legal_name"),
            ) or _first(
                item,
                ("name", "account_name", "insured_name", "legal_name"),
            ) or _first(
                source,
                ("name", "account_name", "insured_name", "legal_name"),
            )
            state_record = hq_location or (locations[0] if locations else None)
            state = _first(
                state_record,
                ("state", "state_code", "primary_state", "risk_state", "address.state"),
            ) or _first(
                source,
                ("state", "state_code", "primary_state", "risk_state", "address.state"),
            )
            occupancy_record = hq_location or (locations[0] if locations else None)
            location_occupancy = _first(
                occupancy_record,
                ("occupancy", "occupancy_type", "building_use", "use_type"),
            )
            location_protection = _first(
                occupancy_record,
                ("protection_class", "public_protection_class", "ppc"),
            )
            policy_tiv = _number(_first(policy, ("tiv", "total_insured_value", "total_tiv"))) if policy else None
            building_models = [
                BuildingEvidence(
                    id=_identifier(building) or f"building-{idx}",
                    year_built=int(year) if (year := _number(_first(building, ("year_built", "construction_year", "built_year")))) is not None else None,
                    construction_type=_text(_first(
                        building,
                        ("construction_type", "construction", "construction_class"),
                    )),
                    tiv=_number(_first(building, ("tiv", "total_insured_value", "value"))),
                    occupancy=_text(
                        _first(
                            building,
                            ("occupancy", "occupancy_type", "building_use", "use_type"),
                        )
                        or location_occupancy
                    ),
                    sprinklered=_boolean(
                        _first(
                            building,
                            ("sprinklered", "has_sprinklers", "sprinkler_status"),
                        )
                    ),
                    protection_class=_text(
                        _first(
                            building,
                            ("protection_class", "public_protection_class", "ppc"),
                        )
                        or location_protection
                    ),
                    flood_zone=_text(_first(
                        building,
                        ("flood_zone", "fema_flood_zone", "flood_risk_zone"),
                    )),
                    wildfire_score=_number(
                        _first(
                            building,
                            ("wildfire_score", "wildfire_risk_score", "wildfire_risk"),
                        )
                    ),
                )
                for idx, building in enumerate(buildings)
            ]
            claim_models = [
                ClaimEvidence(
                    id=_identifier(claim) or f"claim-{idx}",
                    loss_date=_date(
                        _first(claim, ("loss_date", "date_of_loss", "occurred_at"))
                    ),
                    loss_value=_number(
                        _first(
                            claim,
                            ("loss_value", "incurred_loss", "total_incurred", "amount"),
                        )
                    ),
                )
                for idx, claim in enumerate(claims)
            ]
            normalized.append(
                SubmissionEvidence(
                    id=submission_id,
                    submission_number=str(
                        _first(item, ("submission_number", "number", "reference_number"))
                        or f"SUB-{submission_id}"
                    ),
                    insured_name=str(insured_name or "Unnamed account"),
                    received_date=_date(
                        _first(item, ("received_date", "submission_date", "created_at"))
                    ),
                    effective_date=_date(
                        _first(source, ("effective_date", "dates.effective", "policy_start"))
                    ),
                    expiration_date=_date(
                        _first(source, ("expiration_date", "dates.expiration", "policy_end"))
                    ),
                    submission_type=_first(
                        item, ("submission_type", "business_type", "type")
                    )
                    or _first(source, ("business_type", "submission_type", "type")),
                    line_of_business=_first(
                        item, ("line_of_business", "lob", "product_type")
                    )
                    or _first(source, ("line_of_business", "lob", "product_type")),
                    primary_state=str(state).upper() if state is not None else None,
                    tiv=(
                        policy_tiv
                        if policy_tiv is not None
                        else (
                            sum(
                                building.tiv
                                for building in building_models
                                if building.tiv is not None
                            )
                            if any(building.tiv is not None for building in building_models)
                            else None
                        )
                    ),
                    premium=_number(
                        _first(source, ("premium", "total_premium", "written_premium"))
                    ),
                    buildings=building_models,
                    claims=claim_models,
                    raw_records=dict(graph),
                    source_records={
                        resource: [identifier for record in items if (identifier := _identifier(record))]
                        for resource, items in graph.items()
                    },
                    expected_related_records={
                        resource: sorted(identifiers)
                        for resource, identifiers in expected.items()
                    },
                )
            )
        return normalized
