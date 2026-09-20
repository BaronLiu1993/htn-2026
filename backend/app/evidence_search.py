from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from typing import Any

from .evidence_ledger import FactMapper
from .live_data import LiveFederatoLoader, _identifier, _rows
from .models import EvidenceLedger, SubmissionEvidence
from .federato_client import FederatoError, repairable_query_error
from .schema_registry import QueryValidationError


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
        self.policy_search_completed: set[str] = set()

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
        """Expand every selected reference and complete schema-valid projections."""
        prepared = deepcopy(query)
        resource = prepared.get("resource")
        if not isinstance(resource, str):
            return prepared
        registry = self.loader.registry
        select = _normalize_select(prepared.get("select"))
        expand = prepared.get("expand") if isinstance(prepared.get("expand"), dict) else {}
        expand = _expand_selected_references(registry, resource, select, expand)
        if expand:
            prepared["expand"] = expand
            prepared["select"] = _expanded_projection(
                registry,
                self.mapper,
                resource,
                expand,
                select,
            )
        elif select:
            prepared["select"] = select
        if "select" not in prepared:
            return prepared
        prepared["select"] = _omit_unexpanded_references(
            registry,
            resource,
            prepared.get("select"),
            prepared.get("expand") if isinstance(prepared.get("expand"), dict) else {},
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
                elif fact.state == "missing" and fact.note and "linked policy" in fact.note.lower():
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
        needs_buildings = any(
            fact.source.resource.casefold() == "building" or fact.id == "tiv"
            for fact in self.mapper.package.required_facts
        )
        missing = [
            item
            for item in self.submissions
            if (
                item.insured_name == "Unnamed account"
                or item.primary_state is None
                or (needs_buildings and (item.tiv is None or not item.buildings))
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
            include_buildings=needs_buildings,
        )
        if query is None:
            return None
        try:
            return await self._fetch_and_apply(
                gateway,
                query,
                purpose="Need the insured name and risk state" + (", plus building values." if needs_buildings else "."),
                fact_ids=[
                    "primary_state",
                    "tiv",
                    "oldest_building_year",
                    "acceptable_construction_share",
                ] if needs_buildings else ["primary_state"],
                note=_headquarters_note,
            )
        except (FederatoError, QueryValidationError) as exc:
            if not repairable_query_error(exc):
                raise
            return None


    async def fill_policies(self, gateway: Any) -> dict[str, Any] | None:
        """Load premium, business type, and claims even when the agent skips Policy."""

        missing = [
            item
            for item in self.submissions
            if (not _has_linked_policy(item) or item.premium is None)
            and item.id not in self.policy_search_completed
        ]
        if not missing or getattr(gateway, "budget_remaining", 0) <= 0:
            return None
        native_ids = _unique_native_ids(
            self.loader.registry,
            self.loader.registry.find_resource("Submission") or "Submission",
            [item.id for item in missing],
        ) or self.candidate_query_ids
        feedback = await self._fetch_policy_evidence(
            gateway,
            native_ids,
            expand_claims=True,
        )
        still_missing = [
            item.id
            for item in self.submissions
            if not _has_linked_policy(item) or item.premium is None
        ]
        if still_missing and getattr(gateway, "budget_remaining", 0) > 0:
            fallback_ids = _unique_native_ids(
                self.loader.registry,
                self.loader.registry.find_resource("Submission") or "Submission",
                still_missing,
            )
            nested = await self._fetch_policy_evidence(
                gateway,
                fallback_ids,
                expand_claims=False,
                from_submission=True,
            )
            if nested:
                feedback = nested
        if feedback is not None:
            self.policy_search_completed.update(item.id for item in missing)
            for ledger in self.ledgers:
                if ledger.submission_id in {str(item) for item in still_missing}:
                    for fact in ledger.facts:
                        if fact.state == "missing" and "linked Policy" in (fact.note or ""):
                            fact.note = "The completed Federato search returned no linked Policy. Request the Policy record."
        return feedback

    async def _fetch_policy_evidence(
        self,
        gateway: Any,
        native_ids: list[Any],
        *,
        expand_claims: bool,
        from_submission: bool = False,
    ) -> dict[str, Any] | None:
        query = (
            _submission_policy_query(self.loader.registry, native_ids)
            if from_submission
            else _policy_query(self.loader.registry, native_ids, expand_claims=expand_claims)
        )
        if query is None:
            return None
        purpose = (
            "Need the Policy linked from each in-scope Submission."
            if from_submission
            else (
                "Need premium, business type, and claims for the in-scope submissions."
                if expand_claims
                else "Need premium and business type for the in-scope submissions."
            )
        )
        fact_ids = (
            ["premium", "submission_type"]
            if from_submission or not expand_claims
            else ["premium", "submission_type", "five_year_loss_total"]
        )
        try:
            return await self._fetch_and_apply(
                gateway,
                query,
                purpose=purpose,
                fact_ids=fact_ids,
                note=_policy_note,
            )
        except (FederatoError, QueryValidationError) as exc:
            if not repairable_query_error(exc):
                raise
            if from_submission or not expand_claims:
                return None
            return await self._fetch_policy_evidence(
                gateway,
                native_ids,
                expand_claims=False,
            )

    async def _fetch_and_apply(
        self,
        gateway: Any,
        query: dict[str, Any],
        *,
        purpose: str,
        fact_ids: list[str],
        note: Any,
    ) -> dict[str, Any]:
        query = self.prepare_query(query)
        payload = await gateway.query(query, purpose=purpose, fact_ids=fact_ids)
        result = {
            "ok": True,
            "query": query,
            "fact_ids": fact_ids,
            "result": payload,
        }
        feedback = self.apply(result)
        if gateway.trace:
            gateway.trace[-1].result_summary = note(feedback)
            gateway.trace[-1].records_inspected = int(feedback.get("records_found") or 0)
            gateway.trace[-1].facts_changed = int(feedback.get("useful_fact_changes") or 0)
            gateway.trace[-1].source_resource = str(query.get("resource") or "")
            gateway.trace[-1].fact_ids = fact_ids
        gateway.annotate_query(gateway.last_query_audit_id, feedback)
        rows, total = _rows(payload)
        pagination = query.get("pagination") if isinstance(query.get("pagination"), dict) else {}
        limit = int(pagination.get("limit") or 100)
        offset = int(pagination.get("offset") or 0)
        more = len(rows) >= limit and (total is None or offset + len(rows) < total)
        if more and getattr(gateway, "budget_remaining", 0) <= 0:
            raise RuntimeError("Policy/evidence search stopped before all pages were retrieved.")
        if more:
            next_query = deepcopy(query)
            next_query["pagination"] = {"limit": limit, "offset": offset + limit}
            nested = await self._fetch_and_apply(
                gateway,
                next_query,
                purpose=purpose,
                fact_ids=fact_ids,
                note=note,
            )
            feedback["records_found"] = int(feedback.get("records_found") or 0) + int(
                nested.get("records_found") or 0
            )
            feedback["useful_fact_changes"] = int(feedback.get("useful_fact_changes") or 0) + int(
                nested.get("useful_fact_changes") or 0
            )
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
        for ledger in self.ledgers:
            if ledger.submission_id in self.policy_search_completed:
                for fact in ledger.facts:
                    if fact.state == "missing" and "linked Policy" in (fact.note or ""):
                        fact.note = "The completed Federato search returned no linked Policy. Request the Policy record."
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


def _normalize_select(select: Any) -> dict[str, Any]:
    """Turn list/dotted selects into nested objects Federato can expand."""
    if isinstance(select, list):
        output: dict[str, Any] = {}
        for item in select:
            if isinstance(item, str):
                _assign_select_path(output, item, True)
            elif isinstance(item, dict):
                for key, value in _normalize_select(item).items():
                    _merge_select(output, key, value)
        return output
    if not isinstance(select, dict):
        return {}
    expanded = select.get("$expand")
    if expanded is True:
        return {}
    if isinstance(expanded, dict):
        return _normalize_select(expanded.get("select", expanded))
    output: dict[str, Any] = {}
    for key, value in select.items():
        if not isinstance(key, str) or key.startswith("$"):
            continue
        nested = _normalize_select(value) if isinstance(value, (dict, list)) else value
        _assign_select_path(output, key, nested)
    return output


def _assign_select_path(output: dict[str, Any], path: str, value: Any) -> None:
    cursor = output
    segments = path.split(".")
    for segment in segments[:-1]:
        child = cursor.get(segment)
        if not isinstance(child, dict):
            child = {}
            cursor[segment] = child
        cursor = child
    _merge_select(cursor, segments[-1], value)


def _merge_select(output: dict[str, Any], key: str, value: Any) -> None:
    existing = output.get(key)
    if isinstance(existing, dict) and isinstance(value, dict):
        for nested_key, nested_value in value.items():
            _merge_select(existing, nested_key, nested_value)
        return
    if isinstance(existing, dict) and value is True:
        return
    output[key] = value


def _expand_selected_references(
    registry: Any,
    resource: str,
    select: dict[str, Any],
    expand: dict[str, Any],
) -> dict[str, Any]:
    """Add expand for every reference the select tries to traverse."""
    expanded = dict(expand)
    references = {item.field: item for item in registry.references_for(resource)}
    for field, value in select.items():
        reference = references.get(field)
        if reference is None:
            continue
        if value is True and field not in expanded:
            continue
        child_expand = expanded.get(field)
        if child_expand is None:
            child_expand = True
        nested_select = value if isinstance(value, dict) else {}
        nested_expand = (
            {}
            if child_expand is True or child_expand == {}
            else child_expand if isinstance(child_expand, dict) else {}
        )
        nested_expand = _expand_selected_references(
            registry,
            reference.target,
            nested_select,
            nested_expand,
        )
        expanded[field] = nested_expand if nested_expand else True
    return expanded


def _expanded_projection(
    registry: Any,
    mapper: FactMapper,
    resource: str,
    expand: dict[str, Any],
    select: Any,
) -> dict[str, Any]:
    projection = {
        key: value
        for key, value in _normalize_select(select).items()
        if key in registry.fields_for(resource)
    }
    fields = registry.fields_for(resource)
    identifier = registry.identifier_field(resource)
    projection[identifier] = True

    for path in mapper.bound_paths(resource):
        if registry.field_exists(resource, path):
            _assign_select_path(projection, path, True)

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


def _omit_unexpanded_references(registry: Any, resource: str, select: Any, expand: dict[str, Any]) -> dict[str, Any]:
    projection = _normalize_select(select)
    for reference in registry.references_for(resource):
        if reference.field not in projection:
            continue
        if reference.field not in expand:
            # Raw reference IDs are valid. Only nested projections need expansion.
            if projection[reference.field] is not True:
                del projection[reference.field]
            continue
        nested = expand[reference.field]
        child = _omit_unexpanded_references(
            registry, reference.target, projection[reference.field],
            nested if isinstance(nested, dict) else {},
        )
        child[registry.identifier_field(reference.target)] = True
        projection[reference.field] = child
    return projection


def _submission_policy_query(registry: Any, native_ids: list[Any]) -> dict[str, Any] | None:
    submission = registry.find_resource("Submission")
    policy = registry.find_resource("Policy")
    field = _reference_field(registry, submission, policy, "policy", "policies")
    if not submission or not policy or not field or not native_ids:
        return None
    return {
        "resource": submission,
        "where": {registry.identifier_field(submission): {"$in": native_ids}},
        "expand": {field: True},
        "select": {
            registry.identifier_field(submission): True,
            field: _retained_fields(registry, policy, ("premium", "business_type")),
        },
        "pagination": {"limit": 100, "offset": 0},
    }


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
    for alias in extras:
        name = by_key.get(_normalized_key(alias))
        if name:
            wanted.add(name)
    return {name: True for name in sorted(wanted)}


def _headquarters_query(registry: Any, native_ids: list[Any], *, include_buildings: bool = True) -> dict[str, Any] | None:
    if not native_ids:
        return None
    submission = registry.find_resource("Submission")
    insured = registry.find_resource("Insured")
    location = registry.find_resource("Location")
    building = registry.find_resource("Building")
    insured_field = _reference_field(registry, submission, insured, "insured")
    hq_field = _reference_field(registry, insured, location, "hq", "headquarters")
    buildings_field = _reference_field(registry, location, building, "buildings")
    if not submission or not insured or not location or not insured_field or not hq_field:
        return None
    location_select = _retained_fields(registry, location, ("state", "state_code", "primary_state"))
    location_expand: Any = True
    if include_buildings:
        if not building or not buildings_field:
            return None
        location_expand = {buildings_field: True}
        location_select.update(_retained_fields(registry, location, ("occupancy", "protection_class")))
        location_select[buildings_field] = _retained_fields(
            registry, building, ("tiv", "total_insured_value", "year_built", "construction_year",
                                 "construction_type", "construction", "occupancy", "protection_class", "sprinklered"),
        )
    return {
        "resource": submission,
        "where": {registry.identifier_field(submission): {"$in": native_ids}},
        "expand": {insured_field: {hq_field: location_expand}},
        "select": {
            **_retained_fields(registry, submission),
            insured_field: {
                **_retained_fields(registry, insured, ("name", "account_name", "insured_name", "legal_name")),
                hq_field: location_select,
            },
        },
        "pagination": {"limit": 100, "offset": 0},
    }


def _is_headquarters_query(query: dict[str, Any]) -> bool:
    encoded = json.dumps(query.get("expand") or {}).lower()
    return "hq" in encoded or "headquarters" in encoded


def _policy_query(
    registry: Any,
    native_ids: list[Any],
    *,
    expand_claims: bool = True,
    offset: int = 0,
) -> dict[str, Any] | None:
    if not native_ids:
        return None
    policy = registry.find_resource("Policy")
    submission = registry.find_resource("Submission")
    claim = registry.find_resource("Claim")
    submission_field = _reference_field(registry, policy, submission, "submission")
    claims_field = _reference_field(registry, policy, claim, "claims")
    if not policy or not submission or not submission_field:
        return None
    expand: dict[str, Any] = {submission_field: True}
    select: dict[str, Any] = {
        **_retained_fields(
            registry,
            policy,
            ("business_type", "premium", "total_premium", "written_premium", "line_of_business"),
        ),
        submission_field: _retained_fields(registry, submission),
    }
    if expand_claims and claim and claims_field:
        expand[claims_field] = True
        select[claims_field] = _retained_fields(
            registry,
            claim,
            (
                "loss_date",
                "date_of_loss",
                "loss_value",
                "paid_indemnity",
                "paid_expense",
                "reserve_indemnity",
                "reserve_expense",
            ),
        )
    return {
        "resource": policy,
        "where": {submission_field: {"$in": native_ids}},
        "expand": expand,
        "select": select,
        "pagination": {"limit": 100, "offset": offset},
    }


def _headquarters_note(feedback: dict[str, Any]) -> str:
    records = int(feedback.get("records_found") or 0)
    if not records:
        return (
            "The search returned no insured headquarters. "
            "Name, risk state, and building values stay missing."
        )
    confirmed = int(feedback.get("affected_submissions") or 0)
    opener = "The search returned insured headquarters evidence."
    if confirmed:
        noun = "submission" if confirmed == 1 else "submissions"
        return f"{opener} {confirmed} {noun} now have a name and a risk state."
    return f"{opener} Name, risk state, and building values are now on file."


def _policy_note(feedback: dict[str, Any]) -> str:
    records = int(feedback.get("records_found") or 0)
    if not records:
        return "The search returned no linked policies. Premium and claims stay missing."
    confirmed = int(feedback.get("affected_submissions") or 0)
    opener = "The search returned linked policies."
    if confirmed:
        noun = "submission" if confirmed == 1 else "submissions"
        return f"{opener} {confirmed} {noun} now have premium or claims."
    return f"{opener} Premium and claims are now on file."
