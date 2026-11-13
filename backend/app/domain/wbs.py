"""Validated WBS requests and responses, matching the migration-owned schema."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated, Any

from pydantic import Field, StringConstraints, model_validator

from app.domain.common import CamelModel, InputModel

Name = Annotated[str, StringConstraints(min_length=1, max_length=300)]
Description = Annotated[str, StringConstraints(max_length=5000)]
Level = Annotated[int, Field(ge=1, le=3)]
SortOrder = Annotated[int, Field(ge=0, le=2147483647)]
Duration = Annotated[int, Field(ge=0, le=3650)]
Effort = Annotated[Decimal, Field(ge=0, max_digits=8, decimal_places=1, allow_inf_nan=False)]


class WbsNode(CamelModel):
    id: uuid.UUID
    project_id: uuid.UUID
    parent_id: uuid.UUID | None
    level: int
    sort_order: int
    name: str
    description: str | None
    duration_days: int | None
    effort_hours: float | None
    owner_employee_id: uuid.UUID | None


class WbsCreate(InputModel):
    parent_id: uuid.UUID | None = None
    level: Level
    sort_order: SortOrder = 0
    name: Name
    description: Description | None = None
    duration_days: Duration | None = None
    effort_hours: Effort | None = None
    owner_employee_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def validate_parent(self) -> WbsCreate:
        if (self.level == 1) != (self.parent_id is None):
            raise ValueError("Only a level 1 node can have no parent")
        return self

    def values(self) -> dict[str, Any]:
        return self.model_dump(by_alias=False)


class WbsUpdate(InputModel):
    # Moves and changing levels need atomic validation of the whole subtree.
    sort_order: SortOrder | None = None
    name: Name | None = None
    description: Description | None = None
    duration_days: Duration | None = None
    effort_hours: Effort | None = None
    owner_employee_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def validate_changes(self) -> WbsUpdate:
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update")
        if any(getattr(self, f) is None for f in self.model_fields_set & {"name", "sort_order"}):
            raise ValueError("name and sortOrder cannot be null")
        return self

    def changes(self) -> dict[str, Any]:
        return self.model_dump(exclude_unset=True, by_alias=False)
