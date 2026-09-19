from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


Operator = Literal[
    "equals",
    "in",
    "in_normalized",
    "contains_normalized",
    "lt",
    "lte",
    "gt",
    "gte",
    "between",
]


class AppetiteRule(BaseModel):
    id: str
    name: str
    fact: str
    operator: Operator
    value: Any
    expected: str
    review_values: list[Any] = Field(default_factory=list)
    missing_note: str | None = None
    note: str | None = None


class AppetitePack(BaseModel):
    id: str
    name: str
    version: str
    effective_from: date
    source: str
    requirements: list[AppetiteRule]
    preferences: list[AppetiteRule]

    @model_validator(mode="after")
    def validate_unique_rule_ids(self) -> "AppetitePack":
        identifiers = [rule.id for rule in self.requirements + self.preferences]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Appetite rule IDs must be unique.")
        if not self.requirements:
            raise ValueError("An appetite pack must contain at least one requirement.")
        return self


DEFAULT_APPETITE_PATH = (
    Path(__file__).resolve().parent.parent
    / "appetite"
    / "commercial-property-2025.json"
)


def load_appetite(path: Path | None = None) -> AppetitePack:
    rule_path = path or DEFAULT_APPETITE_PATH
    with rule_path.open(encoding="utf-8") as handle:
        return AppetitePack.model_validate(json.load(handle))


DEFAULT_APPETITE = load_appetite()
