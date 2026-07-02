"""Schemas for owner configuration."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field, field_validator, model_validator


class OwnerConfig(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    marker: str = Field(min_length=1, max_length=1)
    is_me: bool

    @field_validator("marker")
    @classmethod
    def validate_marker(cls, value: str) -> str:
        upper = value.strip().upper()
        if len(upper) != 1 or not upper.isalpha():
            raise ValueError("marker must be a single alphabetic character.")
        return upper


class OwnersConfig(BaseModel):
    owners: list[OwnerConfig]

    @model_validator(mode="after")
    def validate_unique_fields(self) -> "OwnersConfig":
        if not self.owners:
            raise ValueError("owners must not be empty.")

        ids = [owner.id for owner in self.owners]
        markers = [owner.marker for owner in self.owners]
        me_count = sum(1 for owner in self.owners if owner.is_me)

        if len(ids) != len(set(ids)):
            raise ValueError("owner ids must be unique.")
        if len(markers) != len(set(markers)):
            raise ValueError("owner markers must be unique.")
        if me_count != 1:
            raise ValueError("Exactly one owner must have is_me=true.")
        return self


def load_owners_config(path: str | Path) -> OwnersConfig:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return OwnersConfig.model_validate(data)
