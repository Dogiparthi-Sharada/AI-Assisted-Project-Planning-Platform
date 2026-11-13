"""Validated project-scope input and output."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import StringConstraints, model_validator

from app.domain.common import CamelModel, InputModel

Category = Literal["objective", "requirement", "deliverable", "constraint", "risk", "missing_info"]
ScopeStatus = Literal["pending", "accepted", "removed"]
Origin = Literal["manual", "ai"]
ScopeText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class ScopeItem(CamelModel):
    id: uuid.UUID
    project_id: uuid.UUID
    category: Category
    text: str
    status: ScopeStatus
    origin: Origin
    sort_order: int
    created_at: datetime
    updated_at: datetime


class ScopeCreate(InputModel):
    category: Category
    text: ScopeText


class ScopeUpdate(InputModel):
    category: Category | None = None
    text: ScopeText | None = None
    status: ScopeStatus | None = None

    @model_validator(mode="after")
    def _validate_changes(self) -> ScopeUpdate:
        if not self.model_fields_set:
            raise ValueError("At least one field is required")
        if any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError("Fields cannot be null")
        return self
