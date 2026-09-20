from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from typing import Any

from .evidence_ledger import FactMapper
from .live_data import LiveFederatoLoader, _identifier, _rows
from .models import EvidenceLedger, SubmissionEvidence


class EvidenceSearch:
    """Keep the source graph and ledger current after each agent-selected query."""

    def __init__(self, loader: LiveFederatoLoader, mapper: FactMapper) -> None:
        self.loader = loader
        self.mapper = mapper
        self.records: dict[str, dict[str, dict[str, Any]]] = {}
        self.retrieved: dict[tuple[str, str, str], datetime] = {}
        self.conflicts: dict[tuple[str, str, str], list[Any]] = {}
        self.pagination_state: dict[str, dict[str, int | bool | None]] = {}
        for resource, rows in loader.records.items():
            for row in rows:
                self._ingest(resource, row)
        self.submissions = loader.normalize(loader.records)
        self.candidate_ids = {item.id for item in self.submissions}
        selection = loader.scope_selection
        submission_resource = selection.resource if selection else loader.registry.find_resource("Submission")
        if selection is not None:
            self.candidate_query_ids = [
                loader.registry.coerce_identifier(
                    selection.resource,
                    row[selection.identifier_field],
                )
                for row in selection.records
                if _identifier(row) in self.candidate_ids
                and selection.identifier_field in row
            ]
        else:
            self.candidate_query_ids = [
                loader.registry.coerce_identifier(submission_resource or "Submission", item_id)
                for item_id in sorted(self.candidate_ids)
            ]
        self.ledgers = [mapper.build(item) for item in self.submissions]
        self.useful_changes = 0

    def rebind(self, bindings: list[Any] | None = None) -> dict[str, Any]:
        bound, unbound = self.mapper.apply_bindings(
            self.ledgers,
            self.loader.registry,
            bindings,
        )
        self.ledgers[:] = [self.mapper.build(item) for item in self.submissions]
        return {
            "bound_fact_ids": bound,
            "unbound_fact_ids": unbound,
            "bindings": [
                self.mapper.bindings[fact_id].model_dump(mode="json")
                for fact_id in [*bound, *unbound]
                if fact_id in self.mapper.bindings
            ],
        }

    def prepare_query(self, query: dict[str, Any]) -> dict[str, Any]:
        """Complete expanded projections with schema-valid evidence fields."""
        prepared = deepcopy(query)
        resource = prepared.get("resource")
        expand = prepared.get("expand")
        if not isinstance(resource, str) or not isinstance(expand, dict):
            return prepared
        prepared["select"] = _expanded_projection(
            self.loader.registry,
            self.mapper,
            resource,
            expand,
            prepared.get("select"),
        )
        return prepared

    def _ingest(self, resource: str, row: dict[str, Any]) -> None:
        identifier = _identifier(row)
        if identifier is None:
            raise ValueError("Evidence queries must retain source record identifiers.")
        stored = self.records.setdefault(resource, {}).setdefault(identifier, {})
        references = {ref.field: ref for ref in self.loader.registry.references_for(resource)}
        for field, value in row.items():
            key = (resource, identifier, field)
            if field in stored and stored[field] is not None and value is not None and stored[field] != value and field not in references:
                self.conflicts.setdefault(key, [stored[field]]).append(value)
            stored[field] = value
            self.retrieved[key] = datetime.now()
            if field in references:
                for child in value if isinstance(value, list) else [value]:
                    if isinstance(child, dict):
                        self._ingest(references[field].target, child)

    def coverage(self) -> dict[str, Any]:
        unresolved_by_reason: dict[str, int] = {}
        for ledger in self.ledgers:
            for fact in ledger.facts:
                if fact.state == "verified":
                    continue
                reason = fact.state
                if fact.state == "missing" and fact.note and "binding" in fact.note.lower():
                    reason = "unbound"
                elif fact.state == "missing" and fact.note and "linked property policy" in fact.note.lower():
                    reason = "missing_policy"
                elif fact.state == "missing" and fact.note and "incomplete" in fact.note.lower():
                    reason = "incomplete_collection"
                unresolved_by_reason[reason] = unresolved_by_reason.get(reason, 0) + 1
        open_questions: dict[str, int] = {}
        for ledger in self.ledgers:
            for fact in ledger.facts:
                if fact.state == "verified":
                    continue
                open_questions[fact.label] = open_questions.get(fact.label, 0) + 1
        submission_resource = (
            self.loader.scope_selection.resource
            if self.loader.scope_selection is not None
            else self.loader.registry.find_resource("Submission") or "Submission"
        )
        no_policy_ids = _unique_native_ids(
            self.loader.registry,
            submission_resource,
            [
                item.id
                for item in self.submissions
                if not _has_linked_policy(item)
            ],
        )
        return {
            "submissions": [
                {"submission_id": ledger.submission_id,
                 "facts": [{"fact_id": fact.fact_id, "question": fact.label,
                            "state": fact.state, "value": fact.value} for fact in ledger.facts]}
                for ledger in self.ledgers
            ],
            "open_questions": [
                {"question": label, "unresolved_accounts": count}
                for label, count in open_questions.items()
            ],
            "unresolved_fact_count": sum(f.state != "verified" for l in self.ledgers for f in l.facts),
            "unresolved_by_reason": unresolved_by_reason,
            "submissions_without_policy": no_policy_ids,
            "submissions_without_policy_count": len(no_policy_ids),
            "relationship_ids": {
                item.id: _native_relationship_ids(
                    item,
                    self.loader.registry,
                )
                for item in self.submissions
            },
            "pagination_state": self.pagination_state,
        }

    async def fill_headquarters(self, gateway: Any) -> dict[str, Any] | None:
        missing = [
            item
            for item in self.submissions
            if (
                item.insured_name == "Unnamed account"
                or item.primary_state is None
                or item.tiv is None
                or not item.buildings
            )
        ]
        if not missing or getattr(gateway, "budget_remaining", 0) <= 0:
            return None
        submission_resource = (
            self.loader.scope_selection.resource
            if self.loader.scope_selection is not None
            else self.loader.registry.find_resource("Submission") or "Submission"
        )
        query = _headquarters_query(
            self.loader.registry,
            _unique_native_ids(
                self.loader.registry,
                submission_resource,
                [item.id for item in missing],
            ),
        )
        if query is None:
            return None
        payload = await gateway.query(
            query,
            purpose="Need the insured name, risk state, and building values.",
            fact_ids=["primary_state", "tiv", "oldest_building_year", "acceptable_construction_share"],
        )
        result = {
            "ok": True,
            "query": query,
            "fact_ids": ["primary_state", "tiv", "oldest_building_year", "acceptable_construction_share"],
            "result": payload,
        }
        feedback = self.apply(result)
        if gateway.trace:
            gateway.trace[-1].result_summary = _headquarters_note(feedback)
            gateway.trace[-1].records_inspected = int(feedback.get("records_found") or 0)
            gateway.trace[-1].facts_changed = int(feedback.get("useful_fact_changes") or 0)
            gateway.trace[-1].source_resource = str(query.get("resource") or "")
            gateway.trace[-1].fact_ids = result["fact_ids"]
        return feedback

    def apply(self, result: dict[str, Any]) -> dict[str, Any]:
        resource = result["query"]["resource"]
        rows, total = _rows(result["result"])
        requested_fact_ids = {
            str(item) for item in result.get("fact_ids", []) if isinstance(item, str)
        }
        before = {(l.submission_id, f.fact_id): (f.state, f.value) for l in self.ledgers for f in l.facts}
        before_sources = {
            item.id: {
                (source_resource, identifier)
                for source_resource, identifiers in item.source_records.items()
                for identifier in identifiers
            }
            for item in self.submissions
        }
        repeated_rows = 0
        missing_identifiers = 0
        for row in rows:
            identifier = _identifier(row)
            if identifier is None:
                missing_identifiers += 1
                continue
            if identifier in self.records.get(resource, {}):
                repeated_rows += 1
            self._ingest(resource, row)
        records = {resource: list(items.values()) for resource, items in self.records.items()}
        # Only queue identities loaded at the start may become assessments.
        self.submissions = [
            item
            for item in self.loader.normalize(records)
            if item.id in self.candidate_ids
        ]
        fresh = [self.mapper.build(item) for item in self.submissions]
        for ledger in fresh:
            for fact in ledger.facts:
                for observation in list(fact.observations):
                    key = (observation.resource, observation.record_id, observation.field_path)
                    observation.retrieved_at = self.retrieved.get(key, observation.retrieved_at)
                    if key in self.conflicts:
                        fact.state = "conflicting"
                        fact.note = "Source observations disagree; confirm the correct value."
                        for value in self.conflicts[key]:
                            fact.observations.append(observation.model_copy(update={"value": value, "state": "conflicting"}))
        prior = {ledger.submission_id: ledger for ledger in self.ledgers}
        for ledger in fresh:
            for fact in ledger.facts:
                old_ledger = prior.get(ledger.submission_id)
                old = old_ledger.fact(fact.fact_id) if old_ledger else None
                known = {json.dumps(item.model_dump(mode="json"), sort_keys=True) for item in fact.observations}
                for observation in old.observations if old else []:
                    encoded = json.dumps(observation.model_dump(mode="json"), sort_keys=True)
                    if encoded not in known:
                        fact.observations.append(observation)
                        known.add(encoded)
        state_changes = sum(
            before.get((ledger.submission_id, fact.fact_id), (None, None))[0]
            != fact.state
            for ledger in fresh
            for fact in ledger.facts
        )
        value_changes = sum(
            before.get((ledger.submission_id, fact.fact_id), (None, None))[1]
            != fact.value
            for ledger in fresh
            for fact in ledger.facts
        )
        changed = sum(
            before.get((ledger.submission_id, fact.fact_id), (None, None))
            != (fact.state, fact.value)
            for ledger in fresh
            for fact in ledger.facts
        )
        newly_resolved = [
            {
                "submission_id": ledger.submission_id,
                "fact_id": fact.fact_id,
            }
            for ledger in fresh
            for fact in ledger.facts
            if before.get((ledger.submission_id, fact.fact_id), (None, None))[0]
            != "verified"
            and fact.state == "verified"
        ]
        owned_pairs = {
            (source_resource, identifier)
            for item in self.submissions
            for source_resource, identifiers in item.source_records.items()
            for identifier in identifiers
        }
        unowned_rows = sum(
            1
            for row in rows
            if (identifier := _identifier(row)) is not None
            and (resource, identifier) not in owned_pairs
        )
        unrelated_rows = sum(
            1
            for row in rows
            if _references_other_submission(
                self.loader.registry,
                resource,
                row,
                self.candidate_ids,
            )
        )
        discovered_relationship_ids = sorted(
            {
                f"{source_resource}:{identifier}"
                for item in self.submissions
                for source_resource, identifier in (
                    {
                        (source_resource, identifier)
                        for source_resource, identifiers in item.source_records.items()
                        for identifier in identifiers
                    }
                    - before_sources.get(item.id, set())
                )
            }
        )
        mapped_fact_ids = sorted(
            {
                fact.fact_id
                for ledger in fresh
                for fact in ledger.facts
                if fact.fact_id in requested_fact_ids
                and before.get((ledger.submission_id, fact.fact_id), (None, None))
                != (fact.state, fact.value)
            }
        )
        repeated_fact_ids = sorted(requested_fact_ids - set(mapped_fact_ids))
        affected_submission_ids = {
            ledger.submission_id
            for ledger in fresh
            if any(
                before.get((ledger.submission_id, fact.fact_id), (None, None))
                != (fact.state, fact.value)
                for fact in ledger.facts
            )
        }
        selected_paths = _selected_paths(result["query"].get("select"))
        missing_projected_fields = sorted(
            path
            for path in selected_paths
            if rows and not any(_path_present(row, path) for row in rows)
        )
        supported_fields = self.mapper.bound_fields(resource) or _supported_fields(self.mapper, resource)
        returned_fields = {
            field
            for row in rows
            for field in row
            if field not in {"id", "_id"}
        }
        reference_fields = {
            reference.field
            for reference in self.loader.registry.references_for(resource)
        }
        unsupported_fields = sorted(
            field
            for field in returned_fields
            if field not in reference_fields
            and _normalized_key(field) not in supported_fields
        )
        pagination = result["query"].get("pagination", {})
        offset = pagination.get("offset", 0) if isinstance(pagination, dict) else 0
        limit = pagination.get("limit", len(rows)) if isinstance(pagination, dict) else len(rows)
        self.pagination_state[resource] = {
            "offset": offset,
            "limit": limit,
            "returned": len(rows),
            "total": total,
            "complete": bool(total is not None and offset + len(rows) >= total)
            or len(rows) < limit,
        }
        self.ledgers[:] = fresh
        self.useful_changes += changed
        if not rows:
            query_effect = "zero_rows"
        elif unowned_rows == len(rows):
            query_effect = "unowned_rows"
        elif unrelated_rows == len(rows):
            query_effect = "unrelated_rows"
        elif repeated_rows == len(rows) and changed == 0:
            query_effect = "repeated_rows"
        elif changed == 0:
            query_effect = "no_fact_change"
        else:
            query_effect = "resolved_facts"
        diagnostics = {
            "records_found": len(rows),
            "records_repeated": repeated_rows,
            "records_missing_identifier": missing_identifiers,
            "mapped_fact_ids": mapped_fact_ids,
            "repeated_fact_ids": repeated_fact_ids,
            "unowned_rows": unowned_rows,
            "unrelated_rows": unrelated_rows,
            "unsupported_fields": unsupported_fields,
            "missing_projected_fields": missing_projected_fields,
            "discovered_relationship_ids": discovered_relationship_ids,
            "state_changes": state_changes,
            "value_changes": value_changes,
            "newly_resolved_facts": newly_resolved,
            "useful_fact_changes": changed,
            "affected_submissions": len(affected_submission_ids),
            "query_effect": query_effect,
        }
        return {**diagnostics, **self.coverage()}


def _normalized_key(value: str) -> str:
    return "".join(character for character in value.lower() if character.isalnum())


def _select_object(select: Any) -> dict[str, Any]:
    if isinstance(select, list):
        return {
            item: True
            for item in select
            if isinstance(item, str)
        }
    if not isinstance(select, dict):
        return {}
    expanded = select.get("$expand")
    if expanded is True:
        return {}
    if isinstance(expanded, dict):
        nested = expanded.get("select")
        return dict(nested) if isinstance(nested, dict) else dict(expanded)
    return dict(select)


def _expanded_projection(
    registry: Any,
    mapper: FactMapper,
    resource: str,
    expand: dict[str, Any],
    select: Any,
) -> dict[str, Any]:
    projection = _select_object(select)
    fields = registry.fields_for(resource)
    identifier = registry.identifier_field(resource)
    projection[identifier] = True

    bound_fields = mapper.bound_fields(resource)
    for field in fields:
        if _normalized_key(field) in bound_fields:
            projection[field] = True

    references = {
        reference.field: reference
        for reference in registry.references_for(resource)
    }
    for field in references:
        projection.setdefault(field, True)

    for field, nested_expand in expand.items():
        reference = references.get(field)
        if reference is None:
            continue
        existing = projection.get(field)
        nested_select = existing if isinstance(existing, (dict, list)) else None
        projection[field] = _expanded_projection(
            registry,
            mapper,
            reference.target,
            nested_expand if isinstance(nested_expand, dict) else {},
            nested_select,
        )
    return projection


def _supported_fields(mapper: FactMapper, resource: str) -> set[str]:
    bound = mapper.bound_fields(resource)
    if bound:
        return {_normalized_key(item) for item in bound}
    fields: set[str] = set()
    for definition in mapper.package.required_facts:
        if _normalized_key(definition.source.resource) != _normalized_key(resource):
            continue
        for value in (
            definition.source.field,
            definition.source.path,
            definition.source.date_field,
            definition.source.weight_field,
            *definition.source.fields,
        ):
            if value:
                fields.add(_normalized_key(value.split(".")[-1]))
    return fields


def _selected_paths(select: Any, prefix: str = "") -> set[str]:
    if isinstance(select, list):
        return {
            f"{prefix}.{item}" if prefix else item
            for item in select
            if isinstance(item, str)
        }
    if not isinstance(select, dict):
        return set()
    output: set[str] = set()
    for key, value in select.items():
        path = f"{prefix}.{key}" if prefix else key
        if value is True:
            output.add(path)
        elif isinstance(value, list):
            output.update(_selected_paths(value, path))
        elif isinstance(value, dict) and not any(
            str(operator).startswith("$") for operator in value
        ):
            output.update(_selected_paths(value, path))
    return output


def _path_present(record: dict[str, Any], path: str) -> bool:
    def descend(current: Any, segments: list[str]) -> bool:
        if not segments:
            return True
        if isinstance(current, list):
            return any(
                descend(item, segments)
                for item in current
                if isinstance(item, dict)
            )
        if not isinstance(current, dict) or segments[0] not in current:
            return False
        return descend(current[segments[0]], segments[1:])

    return descend(record, path.split("."))


def _has_linked_policy(submission: SubmissionEvidence) -> bool:
    for name, identifiers in submission.expected_related_records.items():
        if name.casefold() == "policy" and identifiers:
            return True
    for name, rows in submission.raw_records.items():
        if name.casefold() == "policy" and rows:
            return True
    return False


def _references_other_submission(
    registry: Any,
    resource: str,
    row: dict[str, Any],
    candidate_ids: set[str],
) -> bool:
    submission_resource = registry.find_resource("Submission")
    if resource == submission_resource:
        identifier = _identifier(row)
        return identifier is not None and identifier not in candidate_ids
    for target, identifier in registry.iter_reference_values(resource, row):
        if target == submission_resource and identifier not in candidate_ids:
            return True
    return False


def _native_relationship_ids(
    submission: SubmissionEvidence,
    registry: Any,
) -> dict[str, list[Any]]:
    submission_resource = registry.find_resource("Submission")
    output: dict[str, list[Any]] = {}
    for resource, rows in submission.raw_records.items():
        if resource != submission_resource:
            identifiers = [
                row.get("id") if row.get("id") is not None else row.get("_id")
                for row in rows
            ]
            output.setdefault(resource, []).extend(
                identifier for identifier in identifiers if identifier is not None
            )
        for row in rows:
            references = {
                reference.field: reference
                for reference in registry.references_for(resource)
            }
            for field, reference in references.items():
                raw = row.get(field)
                values = raw if isinstance(raw, list) else [raw]
                for value in values:
                    identifier = (
                        value.get("id")
                        if isinstance(value, dict)
                        else value
                    )
                    if isinstance(value, dict) and identifier is None:
                        identifier = value.get("_id")
                    if (
                        identifier is not None
                        and reference.target != submission_resource
                    ):
                        output.setdefault(reference.target, []).append(identifier)
    return {
        resource: _unique_native_ids(registry, resource, identifiers)
        for resource, identifiers in output.items()
    }


def _unique_native_ids(registry: Any, resource: str, identifiers: list[Any]) -> list[Any]:
    seen: set[str] = set()
    output: list[Any] = []
    for identifier in identifiers:
        native = registry.coerce_identifier(resource, identifier)
        key = registry.canonical_identifier(native)
        if key is None or key in seen:
            continue
        seen.add(key)
        output.append(native)
    return output


def _reference_field(
    registry: Any,
    resource: str | None,
    target: str | None,
    *names: str,
) -> str | None:
    if not resource or not target:
        return None
    wanted = {_normalized_key(name) for name in names if name}
    for reference in registry.references_for(resource):
        if reference.target != target:
            continue
        if not wanted or _normalized_key(reference.field) in wanted:
            return reference.field
    return None


def _retained_fields(registry: Any, resource: str, extras: tuple[str, ...] = ()) -> dict[str, bool]:
    fields = registry.fields_for(resource)
    by_key = {_normalized_key(name): name for name in fields}
    wanted: set[str] = set()
    for candidate in ("id", "_id"):
        if candidate in fields:
            wanted.add(candidate)
    for reference in registry.references_for(resource):
        wanted.add(reference.field)
    for alias in extras:
        name = by_key.get(_normalized_key(alias))
        if name:
            wanted.add(name)
    return {name: True for name in sorted(wanted)}


def _headquarters_query(registry: Any, native_ids: list[Any]) -> dict[str, Any] | None:
    if not native_ids:
        return None
    submission = registry.find_resource("Submission")
    insured = registry.find_resource("Insured")
    location = registry.find_resource("Location")
    building = registry.find_resource("Building")
    insured_field = _reference_field(registry, submission, insured, "insured")
    hq_field = _reference_field(registry, insured, location, "hq", "headquarters")
    buildings_field = _reference_field(registry, location, building, "buildings")
    if not submission or not insured or not location or not building:
        return None
    if not insured_field or not hq_field or not buildings_field:
        return None
    identifier = registry.identifier_field(submission)
    return {
        "resource": submission,
        "where": {identifier: {"$in": native_ids}},
        "expand": {insured_field: {hq_field: {buildings_field: True}}},
        "select": {
            **_retained_fields(registry, submission),
            insured_field: {
                **_retained_fields(
                    registry,
                    insured,
                    ("name", "account_name", "insured_name", "legal_name"),
                ),
                hq_field: {
                    **_retained_fields(
                        registry,
                        location,
                        (
                            "state",
                            "state_code",
                            "primary_state",
                            "occupancy",
                            "protection_class",
                        ),
                    ),
                    buildings_field: _retained_fields(
                        registry,
                        building,
                        (
                            "tiv",
                            "total_insured_value",
                            "year_built",
                            "construction_year",
                            "construction_type",
                            "construction",
                            "occupancy",
                            "protection_class",
                            "sprinklered",
                        ),
                    ),
                },
            },
        },
        "pagination": {"limit": 100, "offset": 0},
    }


def _is_headquarters_query(query: dict[str, Any]) -> bool:
    encoded = json.dumps(query.get("expand") or {}).lower()
    return "hq" in encoded or "headquarters" in encoded


def _headquarters_note(feedback: dict[str, Any]) -> str:
    records = int(feedback.get("records_found") or 0)
    if not records:
        return (
            "The search returned no insured headquarters. "
            "Name, risk state, and building values stay missing."
        )
    confirmed = int(feedback.get("affected_submissions") or 0)
    opener = "The search returned insured headquarters and buildings."
    if confirmed:
        noun = "submission" if confirmed == 1 else "submissions"
        return f"{opener} {confirmed} {noun} now have a name and a risk state."
    return f"{opener} Name, risk state, and building values are now on file."
