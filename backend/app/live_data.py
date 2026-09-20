from __future__ import annotations

import re
import asyncio
from collections import defaultdict, deque
from datetime import date, datetime
from typing import Any, Callable

from .federato_client import FederatoError
from .models import BuildingEvidence, ClaimEvidence, SubmissionEvidence, TraceEvent
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
    if "id" in payload:
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
    value = record.get("id") or record.get("_id")
    return str(value) if value is not None else None


def _claim_total(record: dict[str, Any]) -> float | None:
    direct = _number(
        _first(record, ("loss_value", "incurred_loss", "total_incurred", "amount"))
    )
    if direct is not None:
        return direct
    components = [
        _number(_first(record, (field,)))
        for field in (
            "paid_indemnity",
            "paid_expense",
            "reserve_indemnity",
            "reserve_expense",
        )
    ]
    known = [value for value in components if value is not None]
    return sum(known) if known else None


class LiveFederatoLoader:
    """Loads only package-declared resources and follows discovered references."""

    FIELD_ALIASES = {
        "Submission": (
            "submission_number", "number", "reference_number", "received_date",
            "submission_date", "created_at", "insured_name", "line_of_business",
        ),
        "Policy": (
            "business_type", "line_of_business", "premium", "total_premium",
            "written_premium", "tiv", "total_tiv", "total_insured_value", "dates",
        ),
        "Insured": ("name", "account_name", "insured_name", "legal_name"),
        "Location": (
            "state", "state_code", "primary_state", "risk_state", "address",
            "hazard_tags", "occupancy", "protection_class",
        ),
        "Building": (
            "year_built", "construction_year", "built_year", "construction_type",
            "construction", "construction_class", "tiv", "building_value",
            "contents_value", "business_interruption_value", "occupancy", "sprinklered",
        ),
        "Claim": (
            "loss_date", "date_of_loss", "occurred_at", "loss_value",
            "incurred_loss", "total_incurred", "amount", "paid_indemnity",
            "paid_expense", "reserve_indemnity", "reserve_expense", "status", "litigated",
        ),
        "ExposureUnit": ("basis", "basis_amount", "kind", "classification"),
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

    def _select_fields(self, resource: str, semantic: str) -> list[str]:
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
        return sorted(wanted)

    async def _query_all(self, resource: str, semantic: str) -> list[dict[str, Any]]:
        all_rows: list[dict[str, Any]] = []
        offset = 0
        limit = 100
        selected_fields = self._select_fields(resource, semantic)
        while True:
            query = {
                "resource": resource,
                "pagination": {"limit": limit, "offset": offset},
            }
            if selected_fields:
                query["select"] = selected_fields
            self.registry.validate_query(query)
            try:
                payload = await self.client.query(
                    query,
                    purpose=f"Retrieve {resource} evidence",
                )
                page, total = _rows(payload)
                all_rows.extend(page)
            except Exception as exc:
                raise
            if len(page) < limit or (total is not None and len(all_rows) >= total):
                break
            offset += limit
        return all_rows

    async def load(self) -> list[SubmissionEvidence]:
        resource = self.registry.find_resource("Submission")
        if not resource:
            raise FederatoError("The discovered schema has no Submission resource.")
        self.records = {resource: await self._query_all(resource, "Submission")}
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
                {resource: rows for (resource, _), rows in zip(pending, pages)}
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
        for resource, items in records.items():
            for item in items:
                identifier = _identifier(item)
                if identifier is not None:
                    index[(resource, identifier)] = item
        for (resource, identifier), item in index.items():
            node = (resource, identifier)
            for target_resource, target_id in self.registry.iter_reference_values(resource, item):
                target = (target_resource, target_id)
                if target in index:
                    adjacency[node].add(target)
                    adjacency[target].add(node)


        def related(start: tuple[str, str]) -> dict[str, list[dict[str, Any]]]:
            found: dict[str, list[dict[str, Any]]] = defaultdict(list)
            queue: deque[tuple[tuple[str, str], int]] = deque([(start, 0)])
            seen = {start}
            while queue:
                node, depth = queue.popleft()
                record = index.get(node)
                if record is not None:
                    found[node[0]].append(record)
                if depth >= 4:
                    continue
                for neighbor in adjacency.get(node, set()):
                    if neighbor not in seen:
                        seen.add(neighbor)
                        queue.append((neighbor, depth + 1))
            return found

        normalized: list[SubmissionEvidence] = []
        for item in records[submission_resource]:
            submission_id = _identifier(item)
            if submission_id is None:
                continue
            graph = related((submission_resource, submission_id))
            policy_resource = resource_aliases.get("Policy")
            policies = graph.get(policy_resource, []) if policy_resource else []
            policy = policies[0] if policies else None
            insured_resource = resource_aliases.get("Insured")
            insured = (graph.get(insured_resource, []) or [None])[0] if insured_resource else None
            location_resource = resource_aliases.get("Location")
            locations = graph.get(location_resource, []) if location_resource else []
            building_resource = resource_aliases.get("Building")
            buildings = graph.get(building_resource, []) if building_resource else []
            claim_resource = resource_aliases.get("Claim")
            claims = graph.get(claim_resource, []) if claim_resource else []

            source = policy or item
            insured_name = _first(
                insured or item,
                ("name", "account_name", "insured_name", "legal_name"),
            )
            state = _first(
                locations[0] if locations else source,
                ("state", "state_code", "primary_state", "risk_state"),
            )
            building_models = [
                BuildingEvidence(
                    id=_identifier(building) or f"building-{idx}",
                    year_built=int(year) if (year := _number(_first(building, ("year_built", "construction_year", "built_year")))) is not None else None,
                    construction_type=_first(
                        building,
                        ("construction_type", "construction", "construction_class"),
                    ),
                    tiv=_number(_first(building, ("tiv", "total_insured_value", "value"))),
                    occupancy=_first(
                        building,
                        ("occupancy", "occupancy_type", "building_use", "use_type"),
                    ),
                    sprinklered=_boolean(
                        _first(
                            building,
                            ("sprinklered", "has_sprinklers", "sprinkler_status"),
                        )
                    ),
                    protection_class=_first(
                        building,
                        ("protection_class", "public_protection_class", "ppc"),
                    ),
                    flood_zone=_first(
                        building,
                        ("flood_zone", "fema_flood_zone", "flood_risk_zone"),
                    ),
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
                    loss_value=_claim_total(claim),
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
                    or _first(source, ("business_type", "submission_type")),
                    line_of_business=_first(
                        source, ("line_of_business", "lob", "product_type")
                    ),
                    primary_state=str(state).upper() if state is not None else None,
                    tiv=_number(
                        _first(source, ("tiv", "total_insured_value", "total_tiv"))
                    )
                    or sum(building.tiv or 0 for building in building_models)
                    or None,
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
                )
            )
        return normalized
