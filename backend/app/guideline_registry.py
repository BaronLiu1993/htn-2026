from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


Operator = Literal[
    "equals", "in", "in_normalized", "contains_normalized", "lt", "lte",
    "gt", "gte", "between",
]


class GuidelineRule(BaseModel):
    id: str
    name: str
    fact: str
    operator: Operator
    value: Any
    expected: str
    review_values: list[Any] = Field(default_factory=list)
    missing_note: str | None = None
    note: str | None = None


class ScopeSource(BaseModel):
    resource: str
    field: str
    required: bool = False


class ScopePredicate(BaseModel):
    fact: str
    operator: Operator
    value: Any
    description: str
    source: ScopeSource


FactOperation = Literal[
    "scalar", "minimum", "maximum", "sum", "weighted_match_share", "rolling_sum",
    "rolling_component_sum",
]


class FactSource(BaseModel):
    resource: str
    path: str | None = None
    collection: str | None = None
    field: str | None = None
    fields: list[str] = Field(default_factory=list)
    date_field: str | None = None
    operation: FactOperation = "scalar"
    weight_field: str | None = None
    match_values: list[str] = Field(default_factory=list)
    window_years: int | None = None
    require_all: bool = False
    record_filter: dict[str, str] = Field(default_factory=dict)
    relationship_path: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_shape(self) -> "FactSource":
        if self.operation == "scalar" and not (self.path or self.field):
            raise ValueError("Scalar facts require a source path.")
        if self.operation in {"minimum", "maximum", "sum", "weighted_match_share"} and not (
            self.collection and self.field
        ):
            raise ValueError("Aggregate facts require a collection and field.")
        if self.operation == "rolling_sum" and not (
            self.collection and self.field and self.date_field and self.window_years
        ):
            raise ValueError("Rolling sums require a collection, field, date_field, and window_years.")
        if self.operation == "rolling_component_sum" and not (
            self.collection and self.fields and self.date_field and self.window_years
        ):
            raise ValueError(
                "Rolling component sums require a collection, fields, date_field, and window_years."
            )
        return self


class FactBinding(BaseModel):
    fact_id: str
    resource: str
    operation: FactOperation
    status: Literal["bound", "unbound"] = "bound"
    path: str | None = None
    collection: str | None = None
    field: str | None = None
    fields: list[str] = Field(default_factory=list)
    date_field: str | None = None
    weight_field: str | None = None
    match_values: list[str] = Field(default_factory=list)
    window_years: int | None = None
    require_all: bool = False
    record_filter: dict[str, str] = Field(default_factory=dict)
    relationship_path: list[str] = Field(default_factory=list)
    reason: str | None = None

    def to_source(self) -> FactSource:
        if self.status != "bound":
            raise ValueError(f'Fact "{self.fact_id}" is not bound.')
        return FactSource(
            resource=self.resource,
            path=self.path,
            collection=self.collection,
            field=self.field,
            fields=self.fields,
            date_field=self.date_field,
            operation=self.operation,
            weight_field=self.weight_field,
            match_values=self.match_values,
            window_years=self.window_years,
            require_all=self.require_all,
            record_filter=self.record_filter,
            relationship_path=self.relationship_path,
        )


class RequiredFact(BaseModel):
    id: str
    label: str
    source: FactSource
    display: Literal["plain", "percent", "money"] = "plain"


class SufficiencyPolicy(BaseModel):
    required_rule_kinds: list[Literal["requirement", "preference"]] = Field(
        default_factory=lambda: ["requirement"]
    )
    unresolved_behavior: Literal["needs_review"] = "needs_review"


class RankingTieBreaker(BaseModel):
    field: Literal[
        "target_matches", "evidence_completeness", "disaster_declaration_count", "received_date", "submission_id"
    ]
    direction: Literal["asc", "desc"]


class RankingPolicy(BaseModel):
    status_order: list[str]
    tie_breakers: list[RankingTieBreaker]


class ToolPolicy(BaseModel):
    allowed_tool_classes: list[str]
    required_adapters: list[str]
    optional_adapter_failure: Literal["unavailable", "fail"] = "fail"
    max_calls: int = Field(ge=1, le=100)
    timeout_seconds: float = Field(gt=0, le=300)


class SourcePlan(BaseModel):
    resources: list[str]


class GuidelinePackage(BaseModel):
    id: str
    name: str
    version: str
    effective_from: date
    effective_to: date | None = None
    source: str
    scope: ScopePredicate
    required_facts: list[RequiredFact]
    sufficiency: SufficiencyPolicy
    requirements: list[GuidelineRule]
    preferences: list[GuidelineRule]
    ranking: RankingPolicy
    investigation_profile_id: str | None = None
    tool_policy: ToolPolicy
    source_plan: SourcePlan

    @model_validator(mode="after")
    def validate_contract(self) -> "GuidelinePackage":
        rule_ids = [rule.id for rule in self.requirements + self.preferences]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("Guideline rule IDs must be unique.")
        if not self.requirements:
            raise ValueError("A guideline package must contain at least one requirement.")
        fact_ids = [fact.id for fact in self.required_facts]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("Guideline fact IDs must be unique.")
        known = set(fact_ids)
        referenced = {self.scope.fact} | {
            rule.fact for rule in self.requirements + self.preferences
        }
        missing = sorted(referenced - known)
        if missing:
            raise ValueError(f"Rules reference unknown facts: {', '.join(missing)}.")
        scope_fact = next(fact for fact in self.required_facts if fact.id == self.scope.fact)
        if (
            scope_fact.source.resource != self.scope.source.resource
            or (scope_fact.source.field or scope_fact.source.path) != self.scope.source.field
        ):
            raise ValueError(
                "The scope source must match the declared source of the scope fact."
            )
        if len(self.ranking.status_order) != len(set(self.ranking.status_order)):
            raise ValueError("Ranking status order values must be unique.")
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("Guideline effective_to must not precede effective_from.")
        return self


class GuidelineSummary(BaseModel):
    id: str
    name: str
    version: str
    effective_from: date
    effective_to: date | None = None
    source: str
    scope: str
    required_fact_count: int
    requirement_count: int
    preference_count: int
    investigation_profile_id: str | None = None
    allowed_tools: list[str] = Field(default_factory=list)

    @classmethod
    def from_package(cls, package: GuidelinePackage) -> "GuidelineSummary":
        return cls(
            id=package.id,
            name=package.name,
            version=package.version,
            effective_from=package.effective_from,
            effective_to=package.effective_to,
            source=package.source,
            scope=package.scope.description,
            required_fact_count=len(package.required_facts),
            requirement_count=len(package.requirements),
            preference_count=len(package.preferences),
            investigation_profile_id=package.investigation_profile_id,
            allowed_tools=package.tool_policy.allowed_tool_classes,
        )


GUIDELINES_ROOT = Path(__file__).resolve().parent.parent / "guidelines"
DEFAULT_GUIDELINE_PATH = GUIDELINES_ROOT / "guideline-a" / "package.json"


def load_guideline(path: Path | None = None) -> GuidelinePackage:
    package_path = path or DEFAULT_GUIDELINE_PATH
    with package_path.open(encoding="utf-8") as handle:
        return GuidelinePackage.model_validate(json.load(handle))


class GuidelineRegistry:
    def __init__(self, root: Path = GUIDELINES_ROOT) -> None:
        self.root = root

    def _packages(self) -> list[GuidelinePackage]:
        return [load_guideline(path) for path in sorted(self.root.glob("*/package.json"))]

    def list(self, *, as_of: date | None = None) -> list[GuidelineSummary]:
        selected_date = as_of or date.today()
        return [
            GuidelineSummary.from_package(package)
            for package in self._packages()
            if package.effective_from <= selected_date
            and (package.effective_to is None or selected_date <= package.effective_to)
        ]

    def resolve(
        self,
        package_id: str | None,
        version: str | None = None,
        *,
        as_of: date | None = None,
    ) -> GuidelinePackage:
        selected_date = as_of or date.today()
        candidates = [
            package
            for package in self._packages()
            if (package_id is None or package.id == package_id)
            and (version is None or package.version == version)
            and package.effective_from <= selected_date
            and (package.effective_to is None or selected_date <= package.effective_to)
        ]
        if not candidates:
            target = package_id or "an installed guideline"
            suffix = f" version {version}" if version else ""
            raise LookupError(f"No effective {target}{suffix} was found.")
        if package_id is None and version is None:
            default = next(
                (item for item in candidates if item.id == DEFAULT_GUIDELINE.id),
                candidates[0],
            )
            return default
        if len(candidates) != 1:
            rendered = ", ".join(f"{item.id}@{item.version}" for item in candidates)
            raise LookupError(f"Guideline selection is ambiguous: {rendered}.")
        return candidates[0]


DEFAULT_GUIDELINE = load_guideline()
