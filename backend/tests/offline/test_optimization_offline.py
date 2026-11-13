"""Offline checks for planning primitives and an independent tiny optimum.

Run from backend/: python -m unittest discover -s tests/offline -v
Only the Python standard library and the repository's pure model helpers are
used. These tests do not import or execute Gurobi, OR-Tools, or an API/database.
"""
from __future__ import annotations

import copy
import unittest
from datetime import date
from itertools import product

from app.optimization.baseline import baseline_plan, dependency_offset
from app.optimization.calendar import first_workday, workday_date, workdays_through
from app.optimization.load import allocation_blocks, daily_capacity_tenths, task_daily_tenths
from app.optimization.model import (
    Allocation,
    Constraints,
    Dependency,
    Employee,
    Milestone,
    PlanningInput,
    SkillRequirement,
    SnapshotError,
    Task,
    build_resources,
)

MONDAY = date(2026, 10, 5)


def fixture() -> PlanningInput:
    return PlanningInput(
        project_id="p1",
        project_name="CRM",
        start_date=MONDAY,
        tasks=(
            Task("a", "1.1.1", "Design", 5, owner_id="e1"),
            Task("b", "1.1.2", "Build", 10, owner_id="e1"),
        ),
        employees=(
            Employee("e1", "Mark", hourly_rate_cents=10_000),
            Employee("e2", "Lisa", hourly_rate_cents=12_000),
        ),
        dependencies=(Dependency("a", "b"),),
    )


class OfflinePlanningChecks(unittest.TestCase):
    def test_working_calendar_and_deadline_count(self) -> None:
        self.assertEqual(first_workday(date(2026, 10, 10), 5), date(2026, 10, 12))
        self.assertEqual(first_workday(date(2026, 10, 10), 6), date(2026, 10, 10))
        self.assertEqual(workday_date(MONDAY, 5, 5), date(2026, 10, 12))
        self.assertEqual(workdays_through(MONDAY, date(2026, 10, 16), 5), 10)

    def test_all_dependency_types_and_lag(self) -> None:
        for dep_type, expected in (("FS", 7), ("SS", 2), ("FF", 4), ("SF", -1)):
            with self.subTest(dep_type=dep_type):
                self.assertEqual(dependency_offset(Dependency("a", "b", dep_type, 2), 5, 3), expected)

    def test_baseline_precedence_and_owner(self) -> None:
        plan = baseline_plan(fixture())
        self.assertEqual((plan.tasks["a"].start, plan.tasks["a"].end), (0, 5))
        self.assertEqual((plan.tasks["b"].start, plan.tasks["b"].end), (5, 15))
        self.assertEqual({task.resource_id for task in plan.tasks.values()}, {"e1"})
        self.assertEqual(plan.makespan, 15)

    def test_existing_allocations_and_daily_load(self) -> None:
        inp = fixture()
        inp = PlanningInput(
            project_id=inp.project_id,
            project_name=inp.project_name,
            start_date=inp.start_date,
            tasks=inp.tasks,
            employees=inp.employees,
            dependencies=inp.dependencies,
            allocations=(Allocation("e1", date(2026, 10, 12), 100),),
        )
        self.assertEqual(daily_capacity_tenths(40, 5), 80)
        self.assertEqual(task_daily_tenths(inp.tasks[0]), 80)
        (block,) = allocation_blocks(inp)["e1"]
        self.assertEqual((block.lo, block.hi, block.tenths), (5, 10, 80))

    def test_snapshot_round_trip_and_rejects_invalid(self) -> None:
        inp = fixture()
        inp = PlanningInput(
            project_id=inp.project_id,
            project_name=inp.project_name,
            start_date=inp.start_date,
            tasks=inp.tasks,
            employees=inp.employees,
            dependencies=inp.dependencies,
            constraints=Constraints(must_finish_by=date(2026, 11, 1)),
            milestones=(Milestone("m", "Launch", date(2026, 10, 23), "hard", ("b",)),),
        )
        snapshot = inp.to_snapshot()
        self.assertEqual(PlanningInput.from_snapshot(snapshot), inp)
        broken = copy.deepcopy(snapshot)
        broken["tasks"][0]["duration_days"] = 0
        with self.assertRaises(SnapshotError):
            PlanningInput.from_snapshot(broken)
        broken = copy.deepcopy(snapshot)
        broken["version"] = 999
        with self.assertRaises(SnapshotError):
            PlanningInput.from_snapshot(broken)

    def test_skills_and_contractor_resource(self) -> None:
        inp = fixture()
        req = SkillRequirement("sap", "SAP", 4)
        inp = PlanningInput(
            project_id=inp.project_id,
            project_name=inp.project_name,
            start_date=inp.start_date,
            tasks=(Task("b", "1.2", "Build", 5, skills=(req,)),),
            employees=inp.employees,
            constraints=Constraints(contractor_rate_cents=15_000),
        )
        resources = build_resources(inp)
        self.assertEqual(resources[-1].id, "contractor:sap")
        self.assertEqual(resources[-1].kind, "contractor")
        self.assertEqual(resources[-1].skill_gap(inp.tasks[0]), 0)
        self.assertGreater(resources[0].skill_gap(inp.tasks[0]), 0)

    def test_tiny_model_optimum_by_independent_enumeration(self) -> None:
        # Independent, exhaustive calculation. Two tasks occupy 5 and 10
        # working days, B follows A, two available employees and a 20-day
        # horizon. All amounts are cents. Owner e1 works 40 and 80 hours.
        # The objective includes labor, makespan, baseline shifts, owner changes
        # and peak daily capacity overage. No solver code is imported here.
        rates = {"e1": 10_000, "e2": 12_000}
        candidates = []
        for a, b, owner_a, owner_b in product(range(16), range(11), rates, rates):
            if b < a + 5:
                continue
            labor = 40 * rates[owner_a] + 80 * rates[owner_b]
            makespan_cost = max(a + 5, b + 10) * 20_000
            shift_cost = (a + b - 5) * 1_000
            change_cost = 50_000 * (int(owner_a != "e1") + int(owner_b != "e1"))
            # Each task requires exactly 8h/day and their dependency prevents
            # overlap, so capacity overage is zero for every feasible candidate.
            candidates.append((labor + makespan_cost + shift_cost + change_cost,
                               a, b, owner_a, owner_b))
        self.assertEqual(len(candidates), 84)
        self.assertEqual(min(candidates), (1_500_000, 0, 5, "e1", "e1"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
