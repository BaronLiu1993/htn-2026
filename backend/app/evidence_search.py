from __future__ import annotations

import json
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
        self.skipped_rows = 0
        for resource, rows in loader.records.items():
            for row in rows:
                self._ingest(resource, row)
        self.submissions = loader.normalize(loader.records)
        self.ledgers = [mapper.build(item) for item in self.submissions]
        self.useful_changes = 0

    def _ingest(self, resource: str, row: dict[str, Any]) -> bool:
        identifier = _identifier(row)
        if identifier is None:
            self.skipped_rows += 1
            return False
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
        return True

    def coverage(self) -> dict[str, Any]:
        return {
            "submissions": [
                {"submission_id": ledger.submission_id,
                 "facts": [{"fact_id": fact.fact_id, "question": fact.label,
                            "state": fact.state, "value": fact.value} for fact in ledger.facts]}
                for ledger in self.ledgers
            ],
            "unresolved_fact_count": sum(f.state != "verified" for l in self.ledgers for f in l.facts),
        }

    def apply(self, result: dict[str, Any]) -> dict[str, Any]:
        resource = result["query"]["resource"]
        rows, _ = _rows(result["result"])
        before = {(l.submission_id, f.fact_id): (f.state, f.value) for l in self.ledgers for f in l.facts}
        skipped_before = self.skipped_rows
        for row in rows:
            self._ingest(resource, row)
        records = {resource: list(items.values()) for resource, items in self.records.items()}
        # Only queue identities loaded at the start may become assessments.
        queue_ids = {item.id for item in self.submissions}
        self.submissions = [item for item in self.loader.normalize(records) if item.id in queue_ids]
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
                old = prior[ledger.submission_id].fact(fact.fact_id)
                known = {json.dumps(item.model_dump(mode="json"), sort_keys=True) for item in fact.observations}
                for observation in old.observations if old else []:
                    encoded = json.dumps(observation.model_dump(mode="json"), sort_keys=True)
                    if encoded not in known:
                        fact.observations.append(observation)
                        known.add(encoded)
        changed = sum(before[(l.submission_id, f.fact_id)] != (f.state, f.value) for l in fresh for f in l.facts)
        self.ledgers[:] = fresh
        self.useful_changes += changed
        return {
            "records_found": len(rows),
            "unidentifiable_records": self.skipped_rows - skipped_before,
            "useful_fact_changes": changed,
            **self.coverage(),
        }
