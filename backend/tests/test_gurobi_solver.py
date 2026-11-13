"""Run small, independently checkable scheduling problems with real Gurobi.

These are integration tests: installing gurobipy and a usable Gurobi license is
required. They deliberately do not mock the optimizer or treat another solver
as proof that Gurobi ran. Local tests skip unavailable dependencies/licenses;
GUROBI_TEST_REQUIRED=1 makes them fail instead.
"""
from __future__ import annotations

import os
from datetime import date

import pytest

# CI requires a real Gurobi solve. Local development may omit this optional package.
_GUROBI_REQUIRED = os.environ.get("GUROBI_TEST_REQUIRED") == "1"
if _GUROBI_REQUIRED:
    import gurobipy as gp
else:
    gp = pytest.importorskip("gurobipy", reason="Gurobi integration requires the gurobipy package")

from app.domain.optimization import RunResult  # noqa: E402
from app.optimization.explain import build_report  # noqa: E402
from app.optimization.gurobi_solver import solve_gurobi  # noqa: E402
from app.optimization.model import (  # noqa: E402
    Allocation,
    Constraints,
    Employee,
    SkillRequirement,
    Task,
)
from tests.test_optimization_solver import FAST, MONDAY, make_input, with_constraints  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def require_gurobi_license() -> None:
    """Skip only a genuine missing/unavailable license, not solver bugs."""
    env = gp.Env(empty=True)
    try:
        env.setParam("OutputFlag", 0)
        try:
            env.start()
        except gp.GurobiError as exc:
            if exc.errno == gp.GRB.Error.NO_LICENSE:
                message = "Gurobi is installed but a usable license is unavailable"
                if _GUROBI_REQUIRED:
                    pytest.fail(message, pytrace=False)
                pytest.skip(message)
            raise
    finally:
        env.dispose()


def test_gurobi_solves_known_optimum() -> None:
    # Design must finish before Build. Mark is cheapest and owns both tasks.
    # Independent calculation: 120 h × $100 + 15 days × $200 = $15,000.
    outcome = solve_gurobi(make_input(), FAST)
    assert outcome.status == "optimal"
    assert "gurobi" in outcome.solver.lower()
    assert outcome.solver_version
    assert outcome.plan is not None
    design, build = outcome.plan.tasks["a"], outcome.plan.tasks["b"]
    assert (design.start, design.end, build.start, build.end) == (0, 5, 5, 15)
    assert design.resource_id == build.resource_id == "e1"
    assert outcome.objective == pytest.approx(1_500_000)  # cents
    assert outcome.gap == pytest.approx(0.0)
    assert not outcome.hit_time_limit


def test_gurobi_reports_infeasible_deadline_and_dependency() -> None:
    # Two dependent tasks need 15 working days; only 10 are available.
    inp = with_constraints(make_input(), must_finish_by=date(2026, 10, 16))
    outcome = solve_gurobi(inp, FAST)
    assert outcome.status == "infeasible"
    assert outcome.plan is None
    assert "gurobi" in outcome.solver.lower()
    assert outcome.conflicts  # an infeasible result must explain at least one conflict


def test_hard_capacity_prevents_full_time_task_overlap() -> None:
    inp = make_input(
        tasks=(
            Task("a", "1.1", "A", 5, owner_id="e1"),
            Task("b", "1.2", "B", 5, owner_id="e1"),
        ),
        employees=(Employee("e1", "Solo", hourly_rate_cents=1),),
        dependencies=(),
        constraints=Constraints(capacity_mode="hard", allow_contractors=False),
    )
    outcome = solve_gurobi(inp, FAST)
    assert outcome.status == "optimal"
    assert outcome.plan is not None
    a, b = outcome.plan.tasks["a"], outcome.plan.tasks["b"]
    assert a.resource_id == b.resource_id == "e1"
    assert a.end <= b.start or b.end <= a.start
    assert outcome.plan.makespan == 10


def test_other_project_allocation_blocks_first_week() -> None:
    inp = make_input(
        tasks=(Task("a", "1.1", "A", 5, owner_id="e1"),),
        employees=(Employee("e1", "Busy", hourly_rate_cents=1),),
        dependencies=(),
        allocations=(Allocation("e1", MONDAY, 40),),
        constraints=Constraints(capacity_mode="hard", allow_contractors=False),
    )
    outcome = solve_gurobi(inp, FAST)
    assert outcome.status == "optimal"
    assert outcome.plan is not None
    assert outcome.plan.tasks["a"].start == 5


def test_hard_skill_requirement_forces_qualified_employee() -> None:
    sap = SkillRequirement("sap", "SAP", 4)
    inp = make_input(
        tasks=(Task("b", "1.2", "Build", 5, skills=(sap,)),),
        employees=(
            Employee("e1", "Unqualified", hourly_rate_cents=1),
            Employee("e2", "Qualified", hourly_rate_cents=2, skills={"sap": 5}),
        ),
        dependencies=(),
        constraints=Constraints(skill_mode="hard", allow_contractors=False),
    )
    outcome = solve_gurobi(inp, FAST)
    assert outcome.status == "optimal"
    assert outcome.plan is not None
    assert outcome.plan.tasks["b"].resource_id == "e2"


def test_hard_budget_below_cheapest_labor_is_infeasible() -> None:
    inp = with_constraints(make_input(), budget_total_cents=50_000, budget_mode="hard")
    outcome = solve_gurobi(inp, FAST)
    assert outcome.status == "infeasible"
    assert outcome.plan is None
    assert outcome.conflicts


def test_gurobi_outcome_builds_the_api_report_and_assignment_rows() -> None:
    inp = make_input()
    outcome = solve_gurobi(inp, FAST)
    assert outcome.status == "optimal"
    report = build_report(inp, outcome)
    assert "gurobi" in report.solver.lower()
    assert report.expected_cost_cents == 1_200_000  # $12,000 in labor
    assert report.completion_date == date(2026, 10, 23)
    assert report.result is not None
    RunResult.model_validate(report.result)
    assignments = {a.wbs_node_id: a for a in report.assignments}
    assert set(assignments) == {"a", "b"}
    assert assignments["a"].employee_id == assignments["b"].employee_id == "e1"


def test_soft_deadline_produces_feasible_plan_with_known_penalty() -> None:
    inp = with_constraints(make_input(), must_finish_by=date(2026, 10, 9), deadline_mode="soft")
    outcome = solve_gurobi(inp, FAST)
    assert outcome.status == "optimal"
    assert outcome.plan is not None
    assert outcome.plan.makespan == 15
    # The minimum completion is 10 working days late at $500/day.
    assert outcome.objective == pytest.approx(2_000_000)


def test_missing_hard_skill_with_no_contractors_is_infeasible() -> None:
    inp = make_input(
        tasks=(Task("b", "1.2", "Build", 5, skills=(SkillRequirement("sap", "SAP", 4),)),),
        employees=(Employee("e1", "Unqualified"),),
        dependencies=(),
        constraints=Constraints(skill_mode="hard", allow_contractors=False),
    )
    outcome = solve_gurobi(inp, FAST)
    assert outcome.status == "infeasible"
    assert outcome.plan is None
    assert outcome.conflicts
