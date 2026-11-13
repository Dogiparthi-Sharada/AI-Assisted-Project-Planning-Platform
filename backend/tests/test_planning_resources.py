"""Boundary validation for persisted optimizer inputs."""
from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.domain.planning_resources import (
    ConstraintInput,
    DependencyCreate,
    EmployeeCreate,
    EmployeeUpdate,
    SkillAssignment,
)


def test_employee_normalizes_and_validates_capacity() -> None:
    employee = EmployeeCreate(fullName='  Sam  ', weeklyCapacityHours=20, hourlyRateCents=1000)
    assert employee.full_name == 'Sam'
    assert employee.model_dump(by_alias=False)['weekly_capacity_hours'] == 20
    with pytest.raises(ValidationError):
        EmployeeCreate(fullName='  ')
    with pytest.raises(ValidationError):
        EmployeeCreate(fullName='Sam', weeklyCapacityHours=81)
    with pytest.raises(ValidationError):
        EmployeeCreate(fullName='Sam', hourlyRateCents=-1)


def test_constraint_ranges_dates_and_nan() -> None:
    data = ConstraintInput(startDate='2026-10-01', mustFinishBy='2026-10-31', budgetTotalCents=100)
    assert data.model_dump(by_alias=False)['budget_total_cents'] == 100
    with pytest.raises(ValidationError):
        ConstraintInput(startDate='2026-10-31', mustFinishBy='2026-10-01')
    with pytest.raises(ValidationError):
        ConstraintInput(workingDaysPerWeek=8)
    with pytest.raises(ValidationError):
        ConstraintInput(capacityPenalty=float('nan'))


def test_dependencies_and_skills_reject_invalid_values() -> None:
    uid = uuid.uuid4()
    with pytest.raises(ValidationError):
        DependencyCreate(predecessorId=uid, successorId=uid)
    with pytest.raises(ValidationError):
        DependencyCreate(predecessorId=uid, successorId=uuid.uuid4(), lagDays=366)
    with pytest.raises(ValidationError):
        DependencyCreate(predecessorId=uid, successorId=uuid.uuid4(), type='unknown')
    with pytest.raises(ValidationError):
        SkillAssignment(proficiency=6)


def test_employee_patch_validates_partial_updates_and_nullable_fields() -> None:
    update = EmployeeUpdate(hourlyRateCents=12_500)
    assert update.model_dump(exclude_unset=True, by_alias=False) == {"hourly_rate_cents": 12_500}
    assert EmployeeUpdate(hourlyRateCents=None).model_dump(exclude_unset=True, by_alias=False) == {
        "hourly_rate_cents": None
    }
    for payload in ({}, {"hourlyRateCents": -1}, {"weeklyCapacityHours": 81},
                    {"fullName": " "}, {"active": None}, {"weeklyCapacityHours": None}):
        with pytest.raises(ValidationError):
            EmployeeUpdate.model_validate(payload)
