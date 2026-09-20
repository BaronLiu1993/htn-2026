from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from .guideline_registry import GuidelinePackage, RequiredFact
from .models import (
    EvidenceFact,
    EvidenceItem,
    EvidenceLedger,
    EvidenceObservation,
    SubmissionEvidence,
)
from .schema_registry import SchemaRegistry


def normalized(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()


def _cutoff(as_of: date, years: int) -> date:
    try:
        return as_of.replace(year=as_of.year - years)
    except ValueError:
        return as_of.replace(year=as_of.year - years, day=28)


class FactMapper:
    """Map normalized source records into package-declared canonical facts."""

    def __init__(self, package: GuidelinePackage, *, as_of: date | None = None) -> None:
        self.package = package
        self.as_of = as_of or date.today()
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
        self.registry = registry
        mapped: list[str] = []
        unsupported: list[str] = []
        for definition in self.package.required_facts:
            if registry.find_resource(definition.source.resource):
                mapped.append(definition.id)
                continue
            unsupported.append(definition.id)
            for ledger in ledgers:
                fact = ledger.fact(definition.id)
                if fact and fact.state == "missing":
                    fact.state = "unavailable"
                    fact.note = (
                        f'{definition.source.resource} is not present in the discovered schema.'
                    )
        return mapped, unsupported

    def _collection_complete(self, submission: SubmissionEvidence, resource: str) -> bool:
        if not submission.raw_records:
            # Demo fixtures and the compatibility evaluator are already-normalized
            # snapshots; their explicit collection values are the complete source.
            return True
        target_ids = {str(row.get("id") or row.get("_id")) for name, rows in submission.raw_records.items() if normalized(name) == normalized(resource) for row in rows}
        references = getattr(self, "registry", None)
        if references is None:
            return False
        found = False
        for source_resource, rows in submission.raw_records.items():
            for ref in references.references_for(source_resource):
                if normalized(ref.target) != normalized(resource):
                    continue
                for row in rows:
                    if ref.field not in row or row[ref.field] is None:
                        return False
                    found = True
                    values = row[ref.field] if isinstance(row[ref.field], list) else [row[ref.field]]
                    for value in values:
                        identifier = str(value.get("id") or value.get("_id")) if isinstance(value, dict) else str(value)
                        if identifier not in target_ids:
                            return False
        return found

    def _observation(
        self,
        definition: RequiredFact,
        record_id: str,
        field: str,
        value: Any,
        *,
        source_date: date | None = None,
        state: str = "verified",
    ) -> EvidenceObservation:
        return EvidenceObservation(
            source_system="federato",
            resource=definition.source.resource,
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
        source = definition.source
        if source.operation == "scalar":
            value = payload.get(source.path or "")
            state = "missing" if value is None or value == "" else "verified"
            observations = []
            # Use the actual source field and record, including normalized aliases.
            from .live_data import _first
            for resource, rows in submission.raw_records.items():
                for row in rows:
                    aliases = (source.field or source.path or definition.id, source.path or definition.id)
                    for field in row:
                        if _first({field: row[field]}, aliases) == value and value is not None:
                            observation = self._observation(definition, str(row.get("id") or row.get("_id")), field, value, state=state)
                            observation.resource = resource
                            observations.append(observation)
            if not observations and not submission.raw_records:
                observations = [self._observation(definition, submission.id, source.field or source.path or definition.id, value, state=state)]
            return self._fact(definition, value=value, state=state, observations=observations)

        records = payload.get(source.collection or "") or []
        if not records and source.operation == "rolling_sum" and self._collection_complete(submission, source.resource):
            cutoff = _cutoff(self.as_of, source.window_years or 0)
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
                note=f"No {source.resource} records were available.",
            )
        collection_complete = self._collection_complete(submission, source.resource)
        if source.operation == "minimum":
            observations = [
                self._observation(
                    definition,
                    str(record.get("id") or submission.id),
                    source.field or definition.id,
                    record.get(source.field or ""),
                    state="missing" if record.get(source.field or "") is None else "verified",
                )
                for record in records
            ]
            known = [item.value for item in observations if item.value is not None]
            complete = collection_complete and (len(known) == len(records) or not source.require_all)
            return self._fact(
                definition,
                value=min(known) if known else None,
                state="verified" if known and complete else "missing",
                observations=observations,
                note=None if complete else f"One or more {definition.label.lower()} observations are missing.",
            )
        if source.operation == "weighted_match_share":
            observations = [
                self._observation(
                    definition,
                    str(record.get("id") or submission.id),
                    source.field or definition.id,
                    record.get(source.field or ""),
                    state="missing" if not record.get(source.field or "") else "verified",
                )
                for record in records
            ]
            known = [record for record in records if record.get(source.field or "")]
            if not collection_complete or not known or (source.require_all and len(known) != len(records)):
                return self._fact(
                    definition,
                    value=None,
                    state="missing",
                    observations=observations,
                    note=f"One or more {definition.label.lower()} observations are missing.",
                )
            use_weights = bool(source.weight_field) and all(
                isinstance(record.get(source.weight_field or ""), (int, float))
                and record.get(source.weight_field or "") > 0
                for record in known
            )
            weights = [float(record[source.weight_field]) if use_weights else 1.0 for record in known]
            matched = sum(
                weight
                for record, weight in zip(known, weights)
                if any(
                    token in normalized(record.get(source.field or ""))
                    for token in source.match_values
                )
            )
            return self._fact(
                definition,
                value=matched / sum(weights),
                state="verified",
                observations=observations,
                note="Share is value-weighted." if use_weights else "Share is based on record count.",
            )
        if source.operation == "rolling_sum":
            cutoff = _cutoff(self.as_of, source.window_years or 0)
            observations: list[EvidenceObservation] = []
            total = 0.0
            complete = collection_complete
            for record in records:
                record_id = str(record.get("id") or submission.id)
                source_date = record.get(source.date_field or "")
                value = record.get(source.field or "")
                complete = complete and source_date is not None and value is not None
                observations.extend(
                    [
                        self._observation(
                            definition,
                            record_id,
                            source.date_field or "date",
                            source_date,
                            source_date=source_date,
                            state="missing" if source_date is None else "verified",
                        ),
                        self._observation(
                            definition,
                            record_id,
                            source.field or definition.id,
                            value,
                            source_date=source_date,
                            state="missing" if value is None else "verified",
                        ),
                    ]
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
