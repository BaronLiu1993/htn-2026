from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field


class ProfileDomain(BaseModel):
    id: str
    label: str
    fact_ids: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)


class InvestigationProfile(BaseModel):
    id: str
    name: str
    version: str
    source: str
    domains: list[ProfileDomain]
    source_guidance: list[str] = Field(default_factory=list)
    tool_suggestions: list[str] = Field(default_factory=list)


PROFILES_ROOT = Path(__file__).resolve().parent.parent / "profiles"


class ProfileRegistry:
    def __init__(self, root: Path = PROFILES_ROOT) -> None:
        self.root = root

    def list(self) -> list[InvestigationProfile]:
        return [self._load(path) for path in sorted(self.root.glob("*/profile.json"))]

    def resolve(self, profile_id: str) -> InvestigationProfile:
        matches = [item for item in self.list() if item.id == profile_id]
        if not matches:
            raise LookupError(f'Investigation profile "{profile_id}" is not installed.')
        if len(matches) != 1:
            raise LookupError(f'Investigation profile "{profile_id}" is ambiguous.')
        return matches[0]

    @staticmethod
    def _load(path: Path) -> InvestigationProfile:
        with path.open(encoding="utf-8") as handle:
            return InvestigationProfile.model_validate(json.load(handle))
