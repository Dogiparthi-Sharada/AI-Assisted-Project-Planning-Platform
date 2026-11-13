"""Optimization repository SQL, compiled for PostgreSQL against a recording connection."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError

from app.domain.common import SolverStatus
from app.optimization.explain import build_report
from app.optimization.model import PlanningInput
from app.optimization.solver import solve
from app.repositories.optimization import (
    ActiveRunExistsError,
    MissingDurationsError,
    OptimizationJobStore,
    OptimizationRepository,
    ProjectContext,
)
from tests.test_optimization_solver import FAST, make_input
from tests.test_repositories import Conn, Result

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


class Rows(Result):
    """Result whose rows support attribute access, like SQLAlchemy rows."""

    def __init__(self, rows: list[dict[str, Any]], rowcount: int = 1) -> None:
        super().__init__(rows, rowcount)
        self._rows = [_Row(r) for r in rows]

    def scalar(self) -> Any:
        return next(iter(self._rows[0]._mapping.values())) if self._rows else None


class _Row(SimpleNamespace):
    def __init__(self, mapping: dict[str, Any]) -> None:
        super().__init__(**mapping)
        self._mapping = mapping


def run_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "status": "optimal",
        "solver": "OR-Tools CP-SAT 9.15.6755",
        "runtime_ms": 4300,
        "error": None,
        "infeasibility": None,
        "result": None,
        "created_at": NOW,
        "started_at": NOW,
        "finished_at": NOW,
    }
    row.update(overrides)
    return row


def project_ctx() -> ProjectContext:
    return ProjectContext(
        uuid.uuid4(), uuid.uuid4(), "CRM", date(2026, 10, 5), date(2027, 1, 29), 37_500_000
    )


async def test_project_and_can_write() -> None:
    ctx = project_ctx()
    conn = Conn(Rows([_ctx_dict(ctx)]), Rows([{"x": True}]))
    repo = OptimizationRepository(conn)  # type: ignore[arg-type]
    assert await repo.project(ctx.id) == ctx
    assert await repo.can_write(ctx.id) is True
    assert "private.can_write_project" in conn.sql[1]
    assert await OptimizationRepository(Conn()).project(ctx.id) is None  # type: ignore[arg-type]


def _ctx_dict(ctx: ProjectContext) -> dict[str, Any]:
    return {k: getattr(ctx, k) for k in ProjectContext.__slots__}


async def test_active_run_and_fail() -> None:
    rid = uuid.uuid4()
    conn = Conn(Rows([{"id": rid, "stale": False}]), Rows([]))
    repo = OptimizationRepository(conn)  # type: ignore[arg-type]
    active = await repo.active_run(uuid.uuid4(), 180)
    assert active is not None
    assert (active.id, active.stale) == (rid, False)
    assert "make_interval" in conn.sql[0]
    assert "public.optimization_runs.status IN (" in conn.sql[0]
    await repo.fail_run(rid, "x")
    assert conn.sql[1].startswith("UPDATE public.optimization_runs SET status=")
    assert await repo.active_run(uuid.uuid4(), 180) is None


async def test_get_and_latest_map_columns() -> None:
    row = run_row(infeasibility=[{"name": "n", "explanation": "e"}], status="infeasible")
    conn = Conn(Rows([row]), Rows([run_row(runtime_ms=None, status="queued")]), Rows([]))
    repo = OptimizationRepository(conn)  # type: ignore[arg-type]
    run = await repo.get(row["project_id"], row["id"])
    assert run is not None
    assert run.solve_seconds == 4.3
    assert run.status == SolverStatus.INFEASIBLE
    assert run.iis is not None
    assert run.iis[0].name == "n"
    latest = await repo.latest(uuid.uuid4())
    assert latest is not None
    assert latest.solve_seconds is None
    assert "ORDER BY public.optimization_runs.created_at DESC" in conn.sql[1]
    assert await repo.latest(uuid.uuid4()) is None


class _UniqueError(Exception):
    sqlstate = "23505"


class _OtherError(Exception):
    sqlstate = "23503"


class RaisingConn(Conn):
    def __init__(self, exc: Exception) -> None:
        super().__init__()
        self.exc = exc

    async def execute(self, stmt: Any) -> Result:
        raise IntegrityError("INSERT", {}, self.exc)


async def test_create_maps_unique_violation() -> None:
    conn = Conn()
    rid = await OptimizationRepository(conn).create(uuid.uuid4(), {"version": 1})  # type: ignore[arg-type]
    assert isinstance(rid, uuid.UUID)
    assert conn.sql[0].startswith("INSERT INTO public.optimization_runs")
    with pytest.raises(ActiveRunExistsError):
        await OptimizationRepository(RaisingConn(_UniqueError())).create(uuid.uuid4(), {})  # type: ignore[arg-type]
    with pytest.raises(IntegrityError):
        await OptimizationRepository(RaisingConn(_OtherError())).create(uuid.uuid4(), {})  # type: ignore[arg-type]


async def test_load_input_builds_planning_input() -> None:
    ctx = project_ctx()
    root, d1, wp1, wp2, d2 = (uuid.uuid4() for _ in range(5))
    e1, e2, sap = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    def node(
        id_: uuid.UUID, parent: uuid.UUID | None, level: int, code: str, **kw: Any
    ) -> dict[str, Any]:
        base = {
            "id": id_,
            "parent_id": parent,
            "level": level,
            "name": f"Node {code}",
            "duration_days": None,
            "effort_hours": None,
            "owner_employee_id": None,
            "code": code,
        }
        base.update(kw)
        return base

    conn = Conn(
        Rows(
            [
                node(root, None, 1, "1"),
                node(d1, root, 2, "1.1"),
                node(
                    wp1,
                    d1,
                    3,
                    "1.1.1",
                    duration_days=5,
                    effort_hours=Decimal("30.0"),
                    owner_employee_id=e1,
                ),
                node(wp2, d1, 3, "1.1.2", duration_days=4),
                node(d2, root, 2, "1.2", duration_days=10),  # childless level 2: work package
            ]
        ),
        Rows([{"wbs_node_id": wp1, "skill_id": sap, "name": "SAP", "min_proficiency": 3}]),
        Rows(
            [
                {
                    "id": e1,
                    "full_name": "Mark",
                    "weekly_capacity_hours": Decimal("40.0"),
                    "hourly_rate_cents": 9000,
                    "is_contractor": False,
                },
                {
                    "id": e2,
                    "full_name": "Lisa",
                    "weekly_capacity_hours": Decimal("32.0"),
                    "hourly_rate_cents": None,
                    "is_contractor": True,
                },
            ]
        ),
        Rows([{"employee_id": e1, "skill_id": sap, "proficiency": 4}]),
        # dependency on the summary node d1 expands to both work packages
        Rows([{"predecessor_id": d1, "successor_id": d2, "type": "FS", "lag_days": 2}]),
        Rows([]),  # no constraint set -> project fallbacks
        Rows(
            [
                {
                    "id": uuid.uuid4(),
                    "name": "Phase 1",
                    "due_date": date(2026, 11, 2),
                    "wbs_node_id": d1,
                    "mode": "hard",
                },
                {
                    "id": uuid.uuid4(),
                    "name": "Kickoff",
                    "due_date": date(2026, 10, 5),
                    "wbs_node_id": None,
                    "mode": "soft",
                },
            ]
        ),
        Rows([{"employee_id": e2, "week_start": date(2026, 10, 12), "hours": Decimal("16.0")}]),
    )
    inp = await OptimizationRepository(conn).load_input(ctx, date(2026, 10, 1))  # type: ignore[arg-type]
    assert [t.code for t in inp.tasks] == ["1.1.1", "1.1.2", "1.2"]
    t1 = inp.tasks[0]
    assert (t1.effort_hours, t1.owner_id) == (30.0, str(e1))
    assert t1.skills[0].skill_name == "SAP"
    assert [e.name for e in inp.employees] == ["Mark", "Lisa"]
    assert inp.employees[0].skills == {str(sap): 4}
    assert inp.employees[1].is_contractor
    assert [(d.predecessor_id, d.successor_id, d.lag_days) for d in inp.dependencies] == [
        (str(wp1), str(d2), 2),
        (str(wp2), str(d2), 2),
    ]
    assert inp.start_date == date(2026, 10, 5)
    assert inp.constraints.budget_total_cents == 37_500_000
    assert inp.constraints.must_finish_by == date(2027, 1, 29)
    assert [m.name for m in inp.milestones] == ["Phase 1"]  # nodeless milestone is informational
    assert inp.milestones[0].task_ids == (str(wp1), str(wp2))
    assert inp.allocations[0].hours == 16.0
    assert "wbs_nodes_with_code" in conn.sql[0]
    assert "allocations.project_id != " in conn.sql[-1]
    # The snapshot survives a round trip.
    assert PlanningInput.from_snapshot(inp.to_snapshot()) == inp


@pytest.mark.parametrize("invalid_duration", [None, 0])
async def test_load_input_rejects_missing_work_durations(invalid_duration: int | None) -> None:
    ctx = project_ctx()
    root, summary, good, bad, empty = (uuid.uuid4() for _ in range(5))

    def node(
        node_id: uuid.UUID, parent: uuid.UUID | None, level: int, code: str, duration: int | None
    ) -> dict[str, Any]:
        return {
            "id": node_id,
            "parent_id": parent,
            "level": level,
            "name": f"Node {code}",
            "duration_days": duration,
            "effort_hours": None,
            "owner_employee_id": None,
            "code": code,
        }

    conn = Conn(Rows([
        node(root, None, 1, "1", None),
        node(summary, root, 2, "1.1", None),  # summary: duration is not required
        node(good, summary, 3, "1.1.1", 2),
        node(bad, summary, 3, "1.1.2", invalid_duration),
        node(empty, root, 2, "1.2", invalid_duration),  # childless deliverable is work
    ]))
    with pytest.raises(MissingDurationsError) as exc:
        await OptimizationRepository(conn).load_input(ctx, NOW.date())  # type: ignore[arg-type]
    assert exc.value.items == [
        {"id": str(bad), "code": "1.1.2", "name": "Node 1.1.2"},
        {"id": str(empty), "code": "1.2", "name": "Node 1.2"},
    ]
    assert len(conn.sql) == 1


async def test_load_input_with_constraint_set_and_no_data() -> None:
    cs = {
        "project_id": uuid.uuid4(),
        "budget_total_cents": None,
        "labor_cap_cents": 100,
        "budget_mode": "soft",
        "budget_penalty": 10.0,
        "start_date": date(2026, 11, 2),
        "must_finish_by": None,
        "deadline_mode": "soft",
        "deadline_penalty_per_day": 5.0,
        "working_days_per_week": 6,
        "capacity_mode": "hard",
        "capacity_penalty": 1.0,
        "skill_mode": "soft",
        "allow_contractors": False,
        "contractor_rate_cents": 5,
    }
    # nodes, team, deps, constraint set, milestones (no task ids -> no skills/profs/alloc queries)
    conn = Conn(Rows([]), Rows([]), Rows([]), Rows([cs]), Rows([]))
    ctx = project_ctx()
    inp = await OptimizationRepository(conn).load_input(ctx, date(2026, 10, 1))  # type: ignore[arg-type]
    assert (inp.tasks, inp.employees) == ((), ())
    c = inp.constraints
    assert inp.start_date == date(2026, 11, 2)
    assert c.budget_total_cents == ctx.budget_cents
    assert c.must_finish_by == ctx.target_end_date
    assert (c.labor_cap_cents, c.budget_mode, c.working_days_per_week) == (100, "soft", 6)
    assert (c.capacity_mode, c.skill_mode, c.allow_contractors) == ("hard", "soft", False)
    assert len(conn.sql) == 5


async def test_job_store_claim_finish_fail() -> None:
    snap = make_input().to_snapshot()
    conn = Conn(Rows([{"input_snapshot": snap}]), Rows([]), Rows([]), Rows([]), Rows([]))
    store = OptimizationJobStore(conn)  # type: ignore[arg-type]
    rid = uuid.uuid4()
    assert await store.claim(rid) == snap
    assert "RETURNING public.optimization_runs.input_snapshot" in conn.sql[0]

    uuid_inp = _with_uuid_ids(make_input())
    report = build_report(uuid_inp, solve(uuid_inp, FAST))
    await store.finish(rid, report)
    assert conn.sql[1].startswith("UPDATE public.optimization_runs SET status=")
    assert "result=" in conn.sql[1]
    assert conn.sql[2].startswith("INSERT INTO public.planned_assignments")
    await store.fail(rid, "nope")
    assert "status IN" in conn.sql[3]
    assert await OptimizationJobStore(Conn()).claim(rid) is None  # type: ignore[arg-type]


async def test_job_store_finish_with_recommendations_and_stale() -> None:
    from app.optimization.model import Constraints, SkillRequirement, Task

    inp = _with_uuid_ids(
        make_input(
            tasks=(
                Task(
                    "b",
                    "1.2",
                    "Build",
                    5,
                    owner_id="e1",
                    skills=(SkillRequirement("sap", "SAP", 4),),
                ),
            ),
            dependencies=(),
            constraints=Constraints(),
        )
    )
    report = build_report(inp, solve(inp, FAST))
    assert report.recommendations
    conn = Conn(Rows([]), Rows([]), Rows([]), Rows([], rowcount=3))
    store = OptimizationJobStore(conn)  # type: ignore[arg-type]
    await store.finish(uuid.uuid4(), report)
    assert conn.sql[2].startswith("INSERT INTO public.recommendations")
    assert await store.fail_stale(180, "x") == 3
    assert "make_interval" in conn.sql[3]


def _with_uuid_ids(inp: PlanningInput) -> PlanningInput:
    """Rewrite task/employee ids as UUIDs (the DB rows need them)."""
    import json

    snap = inp.to_snapshot()
    ids = {t["id"] for t in snap["tasks"]} | {e["id"] for e in snap["employees"]}
    text = json.dumps(snap)
    for old in ids:
        text = text.replace(f'"{old}"', f'"{uuid.uuid4()}"')
    return PlanningInput.from_snapshot(json.loads(text))
