from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any

from .guideline_registry import (
    FactBinding,
    FactOperation,
    FactSource,
    GuidelinePackage,
    RequiredFact,
)
from .models import (
    EvidenceFact,
    EvidenceItem,
    EvidenceLedger,
    EvidenceObservation,
    SubmissionEvidence,
)
from .schema_registry import SchemaRegistry

APPROVED_OPERATIONS: set[FactOperation] = {
    "scalar",
    "minimum",
    "maximum",
    "sum",
    "weighted_match_share",
    "rolling_sum",
    "rolling_component_sum",
}

FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "submission_type": ("submission_type", "business_type", "type"),
    "line_of_business": ("line_of_business", "lob", "product_type"),
    "primary_state": ("primary_state", "state", "state_code", "risk_state", "address.state"),
    "tiv": ("tiv", "total_insured_value", "total_tiv"),
    "premium": ("premium", "total_premium", "written_premium"),
    "year_built": ("year_built", "construction_year", "built_year"),
    "construction_type": ("construction_type", "construction", "construction_class"),
    "loss_date": ("loss_date", "date_of_loss", "occurred_at"),
    "loss_value": ("loss_value", "incurred_loss", "total_incurred", "amount"),
}

LOSS_COMPONENT_FIELDS = (
    "paid_indemnity",
    "paid_expense",
    "reserve_indemnity",
    "reserve_expense",
)

RELATED_RESOURCE_HINTS: dict[str, tuple[str, ...]] = {
    "submission_type": ("Submission", "Policy"),
    "line_of_business": ("Submission", "Policy"),
    "primary_state": ("Location", "Policy", "Submission"),
    "tiv": ("Building", "Policy", "ExposureUnit"),
    "premium": ("Policy", "Submission"),
    "oldest_building_year": ("Building",),
    "acceptable_construction_share": ("Building",),
    "five_year_loss_total": ("Claim",),
}


def normalized(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()


def _cutoff(as_of: date, years: int) -> date:
    try:
        return as_of.replace(year=as_of.year - years)
    except ValueError:
        return as_of.replace(year=as_of.year - years, day=28)


def json_safe(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _schema_fields(registry: SchemaRegistry, resource: str) -> dict[str, str]:
    return {
        normalized(name): name
        for name in registry.fields_for(resource)
    }


def _match_field(registry: SchemaRegistry, resource: str, *candidates: str) -> str | None:
    fields = _schema_fields(registry, resource)
    for candidate in candidates:
        if not candidate:
            continue
        matched = fields.get(normalized(candidate.split(".")[-1]))
        if matched:
            return matched
    return None


def _relationship_graph(registry: SchemaRegistry) -> dict[str, list[tuple[str, str]]]:
    graph: dict[str, list[tuple[str, str]]] = {}
    for resource in registry.resource_names():
        for reference in registry.references_for(resource):
            graph.setdefault(resource, []).append((reference.field, reference.target))
            graph.setdefault(reference.target, []).append((reference.field, resource))
    return graph


def _relationship_path(
    registry: SchemaRegistry,
    start: str,
    target: str,
) -> list[str] | None:
    if start == target:
        return []
    queue: list[tuple[str, list[str]]] = [(start, [])]
    seen = {start}
    graph = _relationship_graph(registry)
    while queue:
        current, path = queue.pop(0)
        for field, neighbor in graph.get(current, []):
            if neighbor in seen:
                continue
            next_path = [*path, field]
            if neighbor == target:
                return next_path
            seen.add(neighbor)
            queue.append((neighbor, next_path))
    return None


def _source_collection(definition: RequiredFact, resource: str) -> str | None:
    if definition.source.collection:
        return definition.source.collection
    return {
        "Building": "buildings",
        "Claim": "claims",
        "Location": "locations",
    }.get(resource)


def validate_binding(
    binding: FactBinding,
    package: GuidelinePackage,
    registry: SchemaRegistry,
) -> FactBinding:
    definition = next((item for item in package.required_facts if item.id == binding.fact_id), None)
    if definition is None:
        return binding.model_copy(
            update={
                "status": "unbound",
                "reason": f'Unknown fact "{binding.fact_id}".',
            }
        )
    if definition.source.record_filter and binding.operation != definition.source.operation:
        return binding.model_copy(update={
            "status": "unbound",
            "reason": "The record subset and aggregation are fixed by this guideline fact.",
        })
    if binding.operation not in APPROVED_OPERATIONS:
        return binding.model_copy(
            update={
                "status": "unbound",
                "reason": f'Operation "{binding.operation}" is not an approved deterministic mapping.',
            }
        )
    resource = registry.find_resource(binding.resource)
    if resource is None:
        return binding.model_copy(
            update={
                "status": "unbound",
                "reason": f'Resource "{binding.resource}" is not declared in the live schema.',
            }
        )
    required_paths = [
        path
        for path in (
            binding.field or binding.path,
            *binding.fields,
            binding.date_field,
            binding.weight_field,
            *definition.source.record_filter,
        )
        if path
    ]
    missing = [path for path in required_paths if not registry.field_exists(resource, path)]
    if missing:
        return binding.model_copy(
            update={
                "status": "unbound",
                "reason": f'{resource} does not expose {", ".join(missing)}.',
            }
        )
    start = registry.find_resource("Submission")
    if start and resource != start:
        path = _relationship_path(registry, start, resource)
        if path is None:
            return binding.model_copy(
                update={
                    "status": "unbound",
                    "reason": f'No schema relationship path from Submission to {resource}.',
                }
            )
        binding = binding.model_copy(update={"relationship_path": path})
    collection = binding.collection or definition.source.collection or _source_collection(
        definition, resource
    )
    if binding.operation == "scalar" and not (binding.path or binding.field):
        return binding.model_copy(
            update={
                "status": "unbound",
                "reason": f'Scalar binding for {binding.fact_id} is missing a field path.',
            }
        )
    if binding.operation in {"minimum", "maximum", "sum", "weighted_match_share"} and not (
        collection and binding.field
    ):
        return binding.model_copy(
            update={
                "status": "unbound",
                "reason": f'Aggregate binding for {binding.fact_id} is missing a collection or field.',
            }
        )
    if binding.operation == "rolling_component_sum" and not (
        collection and binding.fields and binding.date_field
    ):
        return binding.model_copy(
            update={
                "status": "unbound",
                "reason": f'Component-sum binding for {binding.fact_id} is missing fields or a date field.',
            }
        )
    return binding.model_copy(
        update={
            "resource": resource,
            "collection": collection,
            "status": "bound",
            "reason": None,
            "match_values": definition.source.match_values,
            "window_years": definition.source.window_years or binding.window_years,
            "require_all": definition.source.require_all,
            "record_filter": definition.source.record_filter,
        }
    )


def discover_bindings(
    package: GuidelinePackage,
    registry: SchemaRegistry,
) -> list[FactBinding]:
    bindings: list[FactBinding] = []
    for definition in package.required_facts:
        source = definition.source
        declared_resource = registry.find_resource(source.resource)
        declared_field = source.field or source.path
        if declared_resource and declared_field and registry.field_exists(declared_resource, declared_field):
            extra_ok = True
            for path in (source.date_field, source.weight_field, *source.fields):
                if path and not registry.field_exists(declared_resource, path):
                    extra_ok = False
                    break
            if extra_ok:
                bindings.append(
                    FactBinding(
                        fact_id=definition.id,
                        resource=declared_resource,
                        operation=source.operation,
                        path=source.path,
                        collection=source.collection,
                        field=source.field or (source.path if source.operation == "scalar" else None),
                        fields=source.fields,
                        date_field=source.date_field,
                        weight_field=source.weight_field,
                        match_values=source.match_values,
                        window_years=source.window_years,
                        require_all=source.require_all,
                        record_filter=source.record_filter,
                    )
                )
                continue
        binding = _discover_equivalent(definition, registry)
        bindings.append(binding)
    return bindings


def _discover_equivalent(definition: RequiredFact, registry: SchemaRegistry) -> FactBinding:
    source = definition.source
    aliases = FIELD_ALIASES.get(
        (source.field or source.path or definition.id).split(".")[-1],
        ((source.field or source.path or definition.id),),
    )
    resources = [
        registry.find_resource(name)
        for name in (source.resource, *RELATED_RESOURCE_HINTS.get(definition.id, ()))
    ]
    seen: set[str] = set()
    for resource in resources:
        if not resource or resource in seen:
            continue
        seen.add(resource)
        if definition.id == "five_year_loss_total":
            date_field = _match_field(registry, resource, source.date_field or "", *FIELD_ALIASES["loss_date"])
            components = [
                field
                for name in LOSS_COMPONENT_FIELDS
                if (field := _match_field(registry, resource, name))
            ]
            if date_field and len(components) >= 2:
                return FactBinding(
                    fact_id=definition.id,
                    resource=resource,
                    operation="rolling_component_sum",
                    collection=_source_collection(definition, resource) or source.collection,
                    fields=components,
                    date_field=date_field,
                    window_years=source.window_years or 5,
                    require_all=source.require_all,
                )
            value_field = _match_field(registry, resource, source.field or "", *FIELD_ALIASES["loss_value"])
            if date_field and value_field:
                return FactBinding(
                    fact_id=definition.id,
                    resource=resource,
                    operation="rolling_sum",
                    collection=_source_collection(definition, resource) or source.collection,
                    field=value_field,
                    date_field=date_field,
                    window_years=source.window_years or 5,
                    require_all=source.require_all,
                )
            continue
        if definition.id == "tiv":
            field = _match_field(registry, resource, *aliases)
            if field and resource != registry.find_resource("Policy"):
                return FactBinding(
                    fact_id=definition.id,
                    resource=resource,
                    operation="sum",
                    collection=_source_collection(definition, resource) or "buildings",
                    field=field,
                    require_all=source.require_all,
                )
            if field:
                return FactBinding(
                    fact_id=definition.id,
                    resource=resource,
                    operation="scalar",
                    path=field,
                    field=field,
                )
            continue
        field = _match_field(registry, resource, *aliases)
        if not field:
            continue
        if source.operation in {"minimum", "maximum", "weighted_match_share", "sum"}:
            weight = _match_field(registry, resource, source.weight_field or "", "tiv") if source.weight_field else None
            return FactBinding(
                fact_id=definition.id,
                resource=resource,
                operation=source.operation,
                collection=_source_collection(definition, resource) or source.collection,
                field=field,
                weight_field=weight,
                match_values=source.match_values,
                require_all=source.require_all,
            )
        return FactBinding(
            fact_id=definition.id,
            resource=resource,
            operation="scalar",
            path=field,
            field=field,
        )
    return FactBinding(
        fact_id=definition.id,
        resource=source.resource,
        operation=source.operation,
        status="unbound",
        reason=f'No schema-valid source was found for {definition.label.lower()}.',
    )


def _record_text(record: dict[str, Any], path: str) -> str | None:
    _, value = _declared_value(record, path)
    if value is None or value == "":
        return None
    return str(value)


def _record_number(record: dict[str, Any], path: str) -> float | None:
    _, value = _declared_value(record, path)
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


def _record_date(record: dict[str, Any], path: str) -> date | None:
    _, value = _declared_value(record, path)
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


def _declared_value(record: dict[str, Any], path: str) -> tuple[str | None, Any]:
    current: Any = record
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            current = None
            break
        current = current[part]
    if current is not None:
        return path, current
    wanted = normalized(path.split(".")[-1])
    for key, value in record.items():
        if normalized(key) == wanted:
            return key, value
    return None, None


class FactMapper:
    """Map normalized source records into package-declared canonical facts."""

    def __init__(self, package: GuidelinePackage, *, as_of: date | None = None) -> None:
        self.package = package
        self.as_of = as_of or date.today()
        self.bindings: dict[str, FactBinding] = {}
        self.registry: SchemaRegistry | None = None
        self.rule_consumers: dict[str, list[str]] = {}
        for rule in package.requirements + package.preferences:
            self.rule_consumers.setdefault(rule.fact, []).append(rule.id)

    def build(self, submission: SubmissionEvidence) -> EvidenceLedger:
        payload = submission.model_dump(mode="python")
        facts = [self._map_fact(submission, payload, definition) for definition in self.package.required_facts]
        return EvidenceLedger(
            submission_id=submission.id,
            guideline_id=self.package.id,
            guideline_version=self.package.version,
            facts=facts,
        )

    def apply_schema_plan(
        self,
        ledgers: list[EvidenceLedger],
        registry: SchemaRegistry,
    ) -> tuple[list[str], list[str]]:
        return self.apply_bindings(ledgers, registry)

    def apply_bindings(
        self,
        ledgers: list[EvidenceLedger],
        registry: SchemaRegistry,
        proposed: list[FactBinding] | None = None,
    ) -> tuple[list[str], list[str]]:
        self.registry = registry
        discovered = {item.fact_id: item for item in discover_bindings(self.package, registry)}
        discovered.update(
            {
                fact_id: binding
                for fact_id, binding in self.bindings.items()
                if binding.status == "bound"
            }
        )
        for item in proposed or []:
            discovered[item.fact_id] = item
        bound: list[str] = []
        unbound: list[str] = []
        for binding in discovered.values():
            validated = validate_binding(binding, self.package, registry)
            current = self.bindings.get(validated.fact_id)
            if validated.status != "bound" and current and current.status == "bound":
                validated = current
            self.bindings[validated.fact_id] = validated
            if validated.status == "bound":
                bound.append(validated.fact_id)
            else:
                unbound.append(validated.fact_id)
                self._mark_unbound(ledgers, validated)
        for definition in self.package.required_facts:
            if definition.id not in self.bindings:
                unbound.append(definition.id)
                self._mark_unbound(
                    ledgers,
                    FactBinding(
                        fact_id=definition.id,
                        resource=definition.source.resource,
                        operation=definition.source.operation,
                        status="unbound",
                        reason="No schema-valid source binding was produced.",
                    ),
                )
        return bound, unbound

    def finalize_unbound(
        self,
        ledgers: list[EvidenceLedger],
        *,
        reason: str = "No valid source binding was found within the evidence budget.",
    ) -> list[str]:
        finalized: list[str] = []
        for definition in self.package.required_facts:
            binding = self.bindings.get(definition.id)
            if binding and binding.status == "bound":
                continue
            finalized.append(definition.id)
            for ledger in ledgers:
                fact = ledger.fact(definition.id)
                if fact and fact.state in {"missing", "unavailable"}:
                    fact.state = "unavailable"
                    fact.note = binding.reason if binding and binding.reason else reason
        return finalized

    def _mark_unbound(self, ledgers: list[EvidenceLedger], binding: FactBinding) -> None:
        for ledger in ledgers:
            fact = ledger.fact(binding.fact_id)
            if fact and fact.state == "missing":
                fact.note = binding.reason or (
                    "The declared source is a hint; a schema-valid binding is still required."
                )

    def _source_for(self, definition: RequiredFact) -> FactSource:
        binding = self.bindings.get(definition.id)
        if binding and binding.status == "bound":
            return binding.to_source()
        return definition.source

    def bound_fields(self, resource: str) -> set[str]:
        return {
            normalized(part).replace(" ", "")
            for path in self.bound_paths(resource)
            for part in (path.split(".")[0], path.split(".")[-1])
        }

    def bound_paths(self, resource: str) -> set[str]:
        fields: set[str] = set()
        for definition in self.package.required_facts:
            source = self._source_for(definition)
            if normalized(source.resource) != normalized(resource):
                continue
            for value in (
                source.field,
                source.path,
                source.date_field,
                source.weight_field,
                *source.fields,
                *source.record_filter,
            ):
                if value:
                    fields.add(value)
        return fields

    def _collection_complete(self, submission: SubmissionEvidence, resource: str) -> bool:
        if not submission.raw_records:
            # Direct typed fixtures are complete snapshots. Live records always carry
            # raw_records and must prove completeness from relationship identifiers.
            return True
        expected_key = next(
            (
                name
                for name in submission.expected_related_records
                if normalized(name) == normalized(resource)
            ),
            None,
        )
        if expected_key is None:
            return False
        expected = set(submission.expected_related_records[expected_key])
        loaded = {
            identifier
            for name, identifiers in submission.source_records.items()
            if normalized(name) == normalized(resource)
            for identifier in identifiers
        }
        return expected == loaded

    def _raw_rows(self, submission: SubmissionEvidence, resource: str) -> list[dict[str, Any]]:
        for name, rows in submission.raw_records.items():
            if normalized(name) == normalized(resource):
                return rows
        return []

    def _has_linked_policy(self, submission: SubmissionEvidence) -> bool:
        for name, identifiers in submission.expected_related_records.items():
            if normalized(name) == "policy" and identifiers:
                return True
        for name, rows in submission.raw_records.items():
            if normalized(name) == "policy" and rows:
                return True
        return False

    def _missing_source_note(self, submission: SubmissionEvidence, source: FactSource) -> str:
        resource = normalized(source.resource)
        if resource in {"policy", "claim", "exposureunit"} and not self._has_linked_policy(
            submission
        ):
            return (
                "No linked Policy has been retrieved, so this fact cannot be confirmed "
                "from source evidence."
            )
        return f"No {source.resource} records were available."

    def _tiv_from_buildings(
        self,
        definition: RequiredFact,
        submission: SubmissionEvidence,
    ) -> tuple[list[EvidenceObservation], float | None]:
        observations: list[EvidenceObservation] = []
        total = 0.0
        found = False
        resource_name = next(
            (name for name in submission.raw_records if normalized(name) == "building"),
            "Building",
        )
        rows = self._raw_rows(submission, "Building")
        if rows:
            for row in rows:
                actual_path, _ = _declared_value(row, "tiv")
                if actual_path is None:
                    actual_path, _ = _declared_value(row, "total_insured_value")
                number = _record_number(row, actual_path or "tiv")
                if number is None:
                    continue
                found = True
                total += number
                observations.append(
                    self._observation(
                        definition,
                        str(row.get("id") or row.get("_id") or submission.id),
                        actual_path or "tiv",
                        number,
                        state="verified",
                        resource=resource_name,
                    )
                )
            return observations, (total if found else None)
        for building in submission.buildings:
            if building.tiv is None:
                continue
            found = True
            total += building.tiv
            observations.append(
                self._observation(
                    definition,
                    building.id,
                    "tiv",
                    building.tiv,
                    state="verified",
                    resource=resource_name,
                )
            )
        return observations, (total if found else None)

    def _observation(
        self,
        definition: RequiredFact,
        record_id: str,
        field: str,
        value: Any,
        *,
        source_date: date | None = None,
        state: str = "verified",
        resource: str | None = None,
    ) -> EvidenceObservation:
        source = self._source_for(definition)
        return EvidenceObservation(
            source_system="federato",
            resource=resource or source.resource,
            record_id=record_id,
            field_path=field,
            value=value,
            source_date=source_date,
            retrieved_at=datetime.now(),
            state=state,
        )

    def _fact(
        self,
        definition: RequiredFact,
        *,
        value: Any,
        state: str,
        observations: list[EvidenceObservation],
        note: str | None = None,
    ) -> EvidenceFact:
        return EvidenceFact(
            fact_id=definition.id,
            label=definition.label,
            value=value,
            state=state,
            requirement_ids=self.rule_consumers.get(definition.id, []),
            observations=observations,
            note=note,
        )

    def _map_fact(
        self,
        submission: SubmissionEvidence,
        payload: dict[str, Any],
        definition: RequiredFact,
    ) -> EvidenceFact:
        source = self._source_for(definition)
        if source.operation == "scalar":
            value = payload.get(source.path or source.field or "")
            if value in (None, ""):
                value = payload.get(definition.source.path or definition.source.field or "")
            state = "missing" if value is None or value == "" else "verified"
            observations: list[EvidenceObservation] = []
            raw_values: list[Any] = []
            source_path = source.field or source.path or definition.id
            for resource, rows in submission.raw_records.items():
                if normalized(resource) != normalized(source.resource):
                    continue
                for row in rows:
                    actual_path, raw_value = _declared_value(row, source_path)
                    if actual_path is None or raw_value is None or raw_value == "":
                        continue
                    raw_values.append(raw_value)
                    observation = self._observation(
                        definition,
                        str(row.get("id") or row.get("_id")),
                        actual_path,
                        raw_value,
                        state="verified",
                    )
                    observation.resource = resource
                    observations.append(observation)
            if definition.id == "tiv" and not observations:
                building_observations, building_sum = self._tiv_from_buildings(
                    definition,
                    submission,
                )
                if building_observations and (
                    not self._has_linked_policy(submission) or value in (None, "")
                ):
                    observations = building_observations
                    raw_values = [item.value for item in building_observations]
                    if value in (None, ""):
                        value = building_sum
                        state = "missing" if value is None else "verified"
            distinct = {json_safe(item) for item in raw_values}
            if len(distinct) > 1 and value not in (None, ""):
                observations = [
                    item
                    for item in observations
                    if json_safe(item.value) == json_safe(value)
                ] or observations[:1]
            elif len(distinct) > 1:
                state = "conflicting"
                value = None
                observations = [
                    item.model_copy(update={"state": "conflicting"})
                    for item in observations
                ]
            if not observations and not submission.raw_records:
                observations = [
                    self._observation(
                        definition,
                        submission.id,
                        source_path,
                        value,
                        state=state,
                    )
                ]
            return self._fact(
                definition,
                value=value,
                state=state,
                observations=observations,
                note=(
                    "Source observations disagree; confirm the correct value."
                    if state == "conflicting"
                    else (
                        self._missing_source_note(submission, source)
                        if state == "missing"
                        and normalized(source.resource) != "submission"
                        else None
                    )
                ),
            )

        records = self._raw_rows(submission, source.resource) or payload.get(source.collection or "") or []
        filter_complete = all(
            _declared_value(record, field)[1] is not None
            for record in records for field in source.record_filter
        )
        if source.record_filter:
            records = [record for record in records if all(
                _declared_value(record, field)[1] == value
                for field, value in source.record_filter.items()
            )]
        if not records and source.operation in {"rolling_sum", "rolling_component_sum"} and self._collection_complete(submission, source.resource):
            cutoff = _cutoff(self.as_of, source.window_years or 0)
            if not submission.raw_records:
                return self._fact(
                    definition,
                    value=0.0,
                    state="verified",
                    observations=[],
                    note=(
                        "The typed source snapshot contains no related claims. "
                        f"Window starts {cutoff.isoformat()}."
                    ),
                )
            return self._fact(
                definition,
                value=0.0,
                state="verified",
                observations=[
                    self._observation(definition, str(row.get("id") or row.get("_id")), ref.field, [], state="verified").model_copy(update={"resource": resource})
                    for resource, rows in submission.raw_records.items()
                    for ref in self.registry.references_for(resource)
                    if normalized(ref.target) == normalized(source.resource)
                    for row in rows if row.get(ref.field) == []
                ],
                note=f"The source confirms no related claims. Window starts {cutoff.isoformat()}.",
            )
        if not records:
            return self._fact(
                definition,
                value=None,
                state="missing",
                observations=[],
                note=self._missing_source_note(submission, source),
            )
        collection_complete = filter_complete and self._collection_complete(submission, source.resource)
        if source.operation in {"minimum", "maximum"}:
            observations = [
                self._observation(
                    definition,
                    str(record.get("id") or record.get("_id") or submission.id),
                    source.field or definition.id,
                    _record_number(record, source.field or ""),
                    state="missing" if _record_number(record, source.field or "") is None else "verified",
                )
                for record in records
            ]
            known = [item.value for item in observations if item.value is not None]
            complete = collection_complete and (len(known) == len(records) or not source.require_all)
            return self._fact(
                definition,
                value=(min(known) if source.operation == "minimum" else max(known)) if known else None,
                state="verified" if known and complete else "missing",
                observations=observations,
                note=None if complete else f"One or more {definition.label.lower()} observations are missing.",
            )
        if source.operation == "sum":
            observations = [
                self._observation(
                    definition,
                    str(record.get("id") or record.get("_id") or submission.id),
                    source.field or definition.id,
                    _record_number(record, source.field or ""),
                    state="missing" if _record_number(record, source.field or "") is None else "verified",
                )
                for record in records
            ]
            known = [item.value for item in observations if item.value is not None]
            complete = collection_complete and (len(known) == len(records) or not source.require_all)
            return self._fact(
                definition,
                value=sum(known) if known and complete else None,
                state="verified" if known and complete else "missing",
                observations=observations,
                note=None if complete else f"One or more {definition.label.lower()} observations are missing.",
            )
        if source.operation == "weighted_match_share":
            observations = [
                self._observation(
                    definition,
                    str(record.get("id") or record.get("_id") or submission.id),
                    source.field or definition.id,
                    _record_text(record, source.field or ""),
                    state="missing" if not _record_text(record, source.field or "") else "verified",
                )
                for record in records
            ]
            known = [record for record in records if _record_text(record, source.field or "")]
            if not collection_complete or not known or (source.require_all and len(known) != len(records)):
                return self._fact(
                    definition,
                    value=None,
                    state="missing",
                    observations=observations,
                    note=f"One or more {definition.label.lower()} observations are missing.",
                )
            use_weights = bool(source.weight_field) and all(
                (weight := _record_number(record, source.weight_field or "")) is not None
                and weight > 0
                for record in known
            )
            weights = [
                float(_record_number(record, source.weight_field or "") or 0)
                if use_weights
                else 1.0
                for record in known
            ]
            matched = sum(
                weight
                for record, weight in zip(known, weights)
                if any(
                    token in normalized(_record_text(record, source.field or ""))
                    for token in source.match_values
                )
            )
            return self._fact(
                definition,
                value=matched / sum(weights) if sum(weights) else None,
                state="verified",
                observations=observations,
                note="Share is value-weighted." if use_weights else "Share is based on record count.",
            )
        if source.operation in {"rolling_sum", "rolling_component_sum"}:
            cutoff = _cutoff(self.as_of, source.window_years or 0)
            observations: list[EvidenceObservation] = []
            total = 0.0
            complete = collection_complete
            component_fields = source.fields or ([source.field] if source.field else [])
            for record in records:
                record_id = str(record.get("id") or record.get("_id") or submission.id)
                source_date = _record_date(record, source.date_field or "")
                if source.operation == "rolling_component_sum":
                    parts = [(_field, _record_number(record, _field)) for _field in component_fields]
                    value = (
                        None
                        if any(part is None for _, part in parts) and source.require_all
                        else sum(part or 0.0 for _, part in parts)
                        if any(part is not None for _, part in parts)
                        else None
                    )
                    for field, part in parts:
                        observations.append(
                            self._observation(
                                definition,
                                record_id,
                                field,
                                part,
                                source_date=source_date,
                                state="missing" if part is None else "verified",
                            )
                        )
                else:
                    value = _record_number(record, source.field or "")
                    observations.append(
                        self._observation(
                            definition,
                            record_id,
                            source.field or definition.id,
                            value,
                            source_date=source_date,
                            state="missing" if value is None else "verified",
                        )
                    )
                complete = complete and source_date is not None and value is not None
                observations.append(
                    self._observation(
                        definition,
                        record_id,
                        source.date_field or "date",
                        source_date,
                        source_date=source_date,
                        state="missing" if source_date is None else "verified",
                    )
                )
                if source_date is not None and value is not None and cutoff <= source_date <= self.as_of:
                    total += float(value)
            return self._fact(
                definition,
                value=total if complete else None,
                state="verified" if complete else "missing",
                observations=observations,
                note=(
                    f"Window starts {cutoff.isoformat()}."
                    if complete
                    else "One or more source records are incomplete."
                ),
            )
        return self._fact(
            definition,
            value=None,
            state="unavailable",
            observations=[],
            note=f'Unsupported fact operation "{source.operation}".',
        )


def evidence_items(fact: EvidenceFact) -> list[EvidenceItem]:
    return [
        EvidenceItem(
            resource=item.resource,
            record_id=item.record_id,
            field=item.field_path,
            value=item.value,
            label=fact.label,
            source_system=item.source_system,
            source_date=item.source_date,
            observed_at=item.retrieved_at,
            fact_id=fact.fact_id,
            state=item.state,
        )
        for item in fact.observations
    ]


def _result_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if not isinstance(value, dict):
        return []
    for key in ("records", "results", "items", "data", "groups"):
        nested = value.get(key)
        rows = _result_rows(nested)
        if rows:
            return rows
    return [value] if "id" in value or "_id" in value else []


def _find_value(record: dict[str, Any], path: str) -> Any:
    current: Any = record
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            current = None
            break
        current = current[part]
    if current is not None:
        return current
    target = normalized(path.split(".")[-1])
    for key, value in record.items():
        if normalized(key) == target:
            return value
    return None
