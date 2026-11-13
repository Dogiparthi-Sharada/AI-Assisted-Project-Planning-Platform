from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from app.domain.common import CamelModel, InputModel

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
EmployeeName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Money = Annotated[int, Field(ge=0, le=9_007_199_254_740_991)]
Mode = Literal['hard', 'soft']


class SkillCreate(InputModel):
    name: Name


class Skill(CamelModel):
    id: uuid.UUID
    org_id: uuid.UUID
    name: str


class EmployeeCreate(InputModel):
    full_name: EmployeeName
    weekly_capacity_hours: Annotated[float, Field(ge=0, le=80, allow_inf_nan=False)] = 40
    hourly_rate_cents: Money | None = None
    role_title: Annotated[str | None, StringConstraints(max_length=120)] = None
    is_contractor: bool = False


class EmployeeUpdate(InputModel):
    full_name: EmployeeName | None = None
    weekly_capacity_hours: Annotated[float | None, Field(ge=0, le=80, allow_inf_nan=False)] = None
    hourly_rate_cents: Money | None = None
    role_title: Annotated[str | None, StringConstraints(max_length=120)] = None
    is_contractor: bool | None = None
    active: bool | None = None

    @model_validator(mode="after")
    def validate_update(self) -> EmployeeUpdate:
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update")
        nonnullable = {"full_name", "weekly_capacity_hours", "is_contractor", "active"}
        if any(getattr(self, field) is None for field in self.model_fields_set & nonnullable):
            raise ValueError("Non-nullable fields cannot be null")
        return self


class Employee(CamelModel):
    id: uuid.UUID
    org_id: uuid.UUID
    full_name: str
    weekly_capacity_hours: float
    hourly_rate_cents: int | None
    role_title: str | None
    is_contractor: bool
    active: bool


class SkillAssignment(InputModel):
    proficiency: Annotated[int, Field(ge=1, le=5)] = 3


class ConstraintInput(InputModel):
    budget_total_cents: Money | None = None
    contingency_pct: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)] = 0
    infra_cap_cents: Money | None = None
    labor_cap_cents: Money | None = None
    budget_mode: Mode = 'hard'
    budget_penalty: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 1000
    start_date: date | None = None
    must_finish_by: date | None = None
    deadline_mode: Mode = 'hard'
    deadline_penalty_per_day: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 500
    working_days_per_week: Annotated[int, Field(ge=1, le=7)] = 5
    capacity_mode: Mode = 'soft'
    capacity_penalty: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 100
    skill_mode: Mode = 'hard'
    allow_contractors: bool = True
    contractor_rate_cents: Money | None = None

    @model_validator(mode='after')
    def validate_dates(self) -> ConstraintInput:
        if self.start_date and self.must_finish_by and self.must_finish_by < self.start_date:
            raise ValueError('mustFinishBy must be on or after startDate')
        return self


class Constraints(ConstraintInput):
    project_id: uuid.UUID


class DependencyCreate(InputModel):
    predecessor_id: uuid.UUID
    successor_id: uuid.UUID
    type: Literal['FS', 'SS', 'FF', 'SF'] = 'FS'
    lag_days: Annotated[int, Field(ge=-365, le=365)] = 0

    @model_validator(mode='after')
    def validate_nodes(self) -> DependencyCreate:
        if self.predecessor_id == self.successor_id:
            raise ValueError('predecessor and successor must differ')
        return self


class Dependency(CamelModel):
    id: uuid.UUID
    project_id: uuid.UUID
    predecessor_id: uuid.UUID
    successor_id: uuid.UUID
    type: Literal['FS', 'SS', 'FF', 'SF']
    lag_days: int


class MilestoneCreate(InputModel):
    name: EmployeeName
    due_date: date
    wbs_node_id: uuid.UUID | None = None
    mode: Mode = 'hard'


class Milestone(CamelModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    due_date: date
    wbs_node_id: uuid.UUID | None
    mode: Mode


class EmployeeSkill(CamelModel):
    id: uuid.UUID
    name: str
    proficiency: int


class EmployeeAllocation(CamelModel):
    week_start: date
    hours: float
