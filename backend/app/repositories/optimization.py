"""Optimization runs: user-side queries (RLS) and the worker's service-side writes."""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.errors import pg_error_info
from app.domain.common import SolverStatus
from app.domain.optimization import OptimizationRun
from app.optimization.explain import Report
from app.optimization.model import (
    Allocation,
    Constraints,
    Dependency,
    Employee,
    Milestone,
    PlanningInput,
    SkillRequirement,
    Task,
)
from app.repositories.tables import (
    allocations,
    constraint_sets,
    dependencies,
    employee_skills,
    employees,
    milestones,
    optimization_runs,
    planned_assignments,
    projects,
    recommendations,
    skills,
    task_skill_requirements,
    wbs_nodes_with_code,
)

ACTIVE = (SolverStatus.QUEUED, SolverStatus.RUNNING)
r = optimization_runs


class ActiveRunExistsError(Exception):
    """The one-active-run-per-project unique index rejected the insert."""


class MissingDurationsError(Exception):
    """Work items must have positive durations before the whole plan can be solved."""

    def __init__(self, items: list[dict[str, str]]) -> None:
        super().__init__("Some work items are missing a positive duration")
        self.items = items


@dataclass(frozen=True, slots=True)
class ProjectContext:
    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    start_date: date | None
    target_end_date: date | None
    budget_cents: int | None


@dataclass(frozen=True, slots=True)
class ActiveRun:
    id: uuid.UUID
    stale: bool


def _run_select() -> sa.Select[Any]:
    return sa.select(
        r.c.id,
        r.c.project_id,
        r.c.status,
        r.c.solver,
        r.c.runtime_ms,
        r.c.error,
        r.c.infeasibility,
        r.c.result,
        r.c.created_at,
        r.c.started_at,
        r.c.finished_at,
    )


def _to_run(row: sa.Row[Any]) -> OptimizationRun:
    m = row._mapping
    return OptimizationRun.model_validate(
        {
            "id": m["id"],
            "project_id": m["project_id"],
            "status": m["status"],
            "solver": m["solver"],
            "solve_seconds": None if m["runtime_ms"] is None else m["runtime_ms"] / 1000,
            "error": m["error"],
            "iis": m["infeasibility"],
            "result": m["result"],
            "created_at": m["created_at"],
            "started_at": m["started_at"],
            "finished_at": m["finished_at"],
        }
    )


def _older_than(seconds: int) -> sa.ColumnElement[bool]:
    cutoff = sa.func.now() - sa.func.make_interval(0, 0, 0, 0, 0, 0, seconds)
    return sa.func.coalesce(r.c.started_at, r.c.created_at) < cutoff


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


class OptimizationRepository:
    """User-facing queries: everything runs under the caller's RLS session."""

    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def project(self, project_id: uuid.UUID) -> ProjectContext | None:
        p = projects
        stmt = sa.select(
            p.c.id, p.c.org_id, p.c.name, p.c.start_date, p.c.target_end_date, p.c.budget_cents
        ).where(p.c.id == project_id)
        row = (await self._conn.execute(stmt)).first()
        return ProjectContext(**row._mapping) if row else None

    async def can_write(self, project_id: uuid.UUID) -> bool:
        stmt = sa.select(sa.func.private.can_write_project(project_id))
        return bool((await self._conn.execute(stmt)).scalar())

    async def active_run(self, project_id: uuid.UUID, stale_after_s: int) -> ActiveRun | None:
        stmt = (
            sa.select(r.c.id, _older_than(stale_after_s).label("stale"))
            .where(r.c.project_id == project_id, r.c.status.in_(ACTIVE))
            .limit(1)
        )
        row = (await self._conn.execute(stmt)).first()
        return ActiveRun(id=row.id, stale=bool(row.stale)) if row else None

    async def fail_run(self, run_id: uuid.UUID, error: str) -> None:
        await self._conn.execute(
            sa.update(r)
            .where(r.c.id == run_id, r.c.status.in_(ACTIVE))
            .values(status=SolverStatus.FAILED, error=error, finished_at=sa.func.now())
        )

    async def create(self, project_id: uuid.UUID, snapshot: dict[str, Any]) -> uuid.UUID:
        # Id generated here and the row re-selected, as for projects (RETURNING
        # visibility depends on SELECT policies).
        run_id = uuid.uuid4()
        try:
            await self._conn.execute(
                sa.insert(r).values(id=run_id, project_id=project_id, input_snapshot=snapshot)
            )
        except IntegrityError as exc:
            if pg_error_info(exc).sqlstate == "23505":
                raise ActiveRunExistsError from exc
            raise
        return run_id

    async def get(self, project_id: uuid.UUID, run_id: uuid.UUID) -> OptimizationRun | None:
        stmt = _run_select().where(r.c.project_id == project_id, r.c.id == run_id)
        row = (await self._conn.execute(stmt)).first()
        return _to_run(row) if row else None

    async def latest(self, project_id: uuid.UUID) -> OptimizationRun | None:
        stmt = (
            _run_select()
            .where(r.c.project_id == project_id)
            .order_by(r.c.created_at.desc(), r.c.id.desc())
            .limit(1)
        )
        row = (await self._conn.execute(stmt)).first()
        return _to_run(row) if row else None

    # ------------------------------------------------------------------ input
    async def load_input(self, project: ProjectContext, today: date) -> PlanningInput:
        """Read the planning data the caller can see and freeze it into a model input.

        Work packages are level-3 nodes and level-2 nodes without children.
        Every work package must have a positive duration; silently ignoring one
        would yield a misleading partial plan. Dependencies and milestones on summary
        nodes are expanded to the work packages underneath them.
        """
        pid = project.id
        w = wbs_nodes_with_code
        nodes = (
            await self._conn.execute(
                sa.select(
                    w.c.id,
                    w.c.parent_id,
                    w.c.level,
                    w.c.name,
                    w.c.duration_days,
                    w.c.effort_hours,
                    w.c.owner_employee_id,
                    w.c.code,
                )
                .where(w.c.project_id == pid)
                .order_by(w.c.path)
            )
        ).all()
        children: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
        for n in nodes:
            if n.parent_id is not None:
                children[n.parent_id].append(n.id)
        work = [n for n in nodes if n.level == 3 or (n.level == 2 and not children.get(n.id))]
        invalid = [n for n in work if n.duration_days is None or n.duration_days <= 0]
        if invalid:
            raise MissingDurationsError(
                [{"id": str(n.id), "code": n.code, "name": n.name} for n in invalid]
            )
        task_ids = {n.id for n in work}

        def leaves(node_id: uuid.UUID) -> list[uuid.UUID]:
            if node_id in task_ids:
                return [node_id]
            return [leaf for child in children.get(node_id, []) for leaf in leaves(child)]

        reqs = await self._requirements(task_ids)
        team, profs = await self._team(project.org_id)
        emp_ids = [row.id for row in team]

        d = dependencies
        dep_rows = (
            await self._conn.execute(
                sa.select(d.c.predecessor_id, d.c.successor_id, d.c.type, d.c.lag_days)
                .where(d.c.project_id == pid)
                .order_by(d.c.predecessor_id, d.c.successor_id)
            )
        ).all()
        deps: dict[tuple[str, str], Dependency] = {}
        for row in dep_rows:
            for a in leaves(row.predecessor_id):
                for b in leaves(row.successor_id):
                    if a != b:
                        deps.setdefault(
                            (str(a), str(b)),
                            Dependency(str(a), str(b), str(row.type), int(row.lag_days)),  # type: ignore[arg-type]
                        )

        cs = (
            await self._conn.execute(
                sa.select(constraint_sets).where(constraint_sets.c.project_id == pid)
            )
        ).first()
        constraints, start = self._constraints(cs, project, today)

        ms = milestones
        milestone_list = []
        for row in (
            await self._conn.execute(
                sa.select(ms.c.id, ms.c.name, ms.c.due_date, ms.c.wbs_node_id, ms.c.mode)
                .where(ms.c.project_id == pid)
                .order_by(ms.c.due_date, ms.c.id)
            )
        ).all():
            covered = leaves(row.wbs_node_id) if row.wbs_node_id is not None else []
            if covered:
                milestone_list.append(
                    Milestone(
                        id=str(row.id),
                        name=row.name,
                        due_date=row.due_date,
                        mode=str(row.mode),  # type: ignore[arg-type]
                        task_ids=tuple(str(x) for x in covered),
                    )
                )

        alloc_list = await self._allocations(emp_ids, pid, start)

        return PlanningInput(
            project_id=str(pid),
            project_name=project.name,
            start_date=start,
            tasks=tuple(
                Task(
                    id=str(n.id),
                    code=n.code,
                    name=n.name,
                    duration_days=int(n.duration_days),
                    effort_hours=None if n.effort_hours is None else float(n.effort_hours),
                    owner_id=None if n.owner_employee_id is None else str(n.owner_employee_id),
                    skills=tuple(reqs.get(n.id, ())),
                )
                for n in work
            ),
            employees=tuple(
                Employee(
                    id=str(row.id),
                    name=row.full_name,
                    weekly_capacity_hours=float(row.weekly_capacity_hours),
                    hourly_rate_cents=row.hourly_rate_cents,
                    is_contractor=bool(row.is_contractor),
                    skills=profs.get(row.id, {}),
                )
                for row in team
            ),
            dependencies=tuple(deps.values()),
            constraints=constraints,
            milestones=tuple(milestone_list),
            allocations=tuple(alloc_list),
        )

    async def _requirements(
        self, task_ids: set[uuid.UUID]
    ) -> dict[uuid.UUID, list[SkillRequirement]]:
        reqs: dict[uuid.UUID, list[SkillRequirement]] = defaultdict(list)
        if task_ids:
            t, s = task_skill_requirements, skills
            rows = (
                await self._conn.execute(
                    sa.select(t.c.wbs_node_id, t.c.skill_id, s.c.name, t.c.min_proficiency)
                    .select_from(t.join(s, s.c.id == t.c.skill_id))
                    .where(t.c.wbs_node_id.in_(task_ids))
                    .order_by(t.c.wbs_node_id, s.c.name)
                )
            ).all()
            for row in rows:
                reqs[row.wbs_node_id].append(
                    SkillRequirement(str(row.skill_id), str(row.name), int(row.min_proficiency))
                )
        return reqs

    async def _team(
        self, org_id: uuid.UUID
    ) -> tuple[Sequence[sa.Row[Any]], dict[uuid.UUID, dict[str, int]]]:
        e = employees
        team = (
            await self._conn.execute(
                sa.select(
                    e.c.id,
                    e.c.full_name,
                    e.c.weekly_capacity_hours,
                    e.c.hourly_rate_cents,
                    e.c.is_contractor,
                )
                .where(e.c.org_id == org_id, e.c.active.is_(True))
                .order_by(e.c.full_name, e.c.id)
            )
        ).all()
        profs: dict[uuid.UUID, dict[str, int]] = defaultdict(dict)
        emp_ids = [row.id for row in team]
        if emp_ids:
            es = employee_skills
            for row in (
                await self._conn.execute(
                    sa.select(es.c.employee_id, es.c.skill_id, es.c.proficiency).where(
                        es.c.employee_id.in_(emp_ids)
                    )
                )
            ).all():
                profs[row.employee_id][str(row.skill_id)] = int(row.proficiency)
        return team, profs

    async def _allocations(
        self, emp_ids: list[uuid.UUID], pid: uuid.UUID, start: date
    ) -> list[Allocation]:
        alloc_list: list[Allocation] = []
        if emp_ids:
            al = allocations
            for row in (
                await self._conn.execute(
                    sa.select(al.c.employee_id, al.c.week_start, al.c.hours)
                    .where(
                        al.c.employee_id.in_(emp_ids),
                        al.c.project_id != pid,
                        al.c.week_start >= _monday(start),
                    )
                    .order_by(al.c.employee_id, al.c.week_start)
                )
            ).all():
                alloc_list.append(
                    Allocation(str(row.employee_id), row.week_start, float(row.hours))
                )
        return alloc_list

    @staticmethod
    def _constraints(
        cs: sa.Row[Any] | None, project: ProjectContext, today: date
    ) -> tuple[Constraints, date]:
        """Constraint set with fallbacks to the project's own dates and budget."""
        start = (cs.start_date if cs else None) or project.start_date or today
        if cs is None:
            return (
                Constraints(
                    budget_total_cents=project.budget_cents,
                    must_finish_by=project.target_end_date,
                ),
                start,
            )
        return (
            Constraints(
                budget_total_cents=(
                    cs.budget_total_cents
                    if cs.budget_total_cents is not None
                    else project.budget_cents
                ),
                labor_cap_cents=cs.labor_cap_cents,
                budget_mode=str(cs.budget_mode),  # type: ignore[arg-type]
                budget_penalty=float(cs.budget_penalty),
                must_finish_by=cs.must_finish_by or project.target_end_date,
                deadline_mode=str(cs.deadline_mode),  # type: ignore[arg-type]
                deadline_penalty_per_day=float(cs.deadline_penalty_per_day),
                working_days_per_week=int(cs.working_days_per_week),
                capacity_mode=str(cs.capacity_mode),  # type: ignore[arg-type]
                capacity_penalty=float(cs.capacity_penalty),
                skill_mode=str(cs.skill_mode),  # type: ignore[arg-type]
                allow_contractors=bool(cs.allow_contractors),
                contractor_rate_cents=cs.contractor_rate_cents,
            ),
            start,
        )


# --------------------------------------------------------------------------- worker side
class OptimizationJobStore:
    """Writes made by the background worker on a service connection (bypasses RLS).

    `planned_assignments` and `recommendations` have no user write policies:
    only this trusted job writes them.
    """

    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def claim(self, run_id: uuid.UUID) -> dict[str, Any] | None:
        """Move a queued run to running; returns its input snapshot (None if not queued)."""
        stmt = (
            sa.update(r)
            .where(r.c.id == run_id, r.c.status == SolverStatus.QUEUED)
            .values(status=SolverStatus.RUNNING, started_at=sa.func.now())
            .returning(r.c.input_snapshot)
        )
        row = (await self._conn.execute(stmt)).first()
        return dict(row.input_snapshot) if row else None

    async def finish(self, run_id: uuid.UUID, report: Report) -> None:
        await self._conn.execute(
            sa.update(r)
            .where(r.c.id == run_id)
            .values(
                status=report.status,
                solver=report.solver,
                solver_version=report.solver_version,
                runtime_ms=report.runtime_ms,
                mip_gap=report.mip_gap,
                objective_value=report.objective_value,
                expected_cost_cents=report.expected_cost_cents,
                completion_date=report.completion_date,
                avg_utilization=report.avg_utilization,
                critical_path=report.critical_path,
                result=report.result,
                infeasibility=report.iis,
                error=report.error,
                finished_at=sa.func.now(),
            )
        )
        if report.assignments:
            await self._conn.execute(
                sa.insert(planned_assignments).values(
                    [
                        {
                            "run_id": run_id,
                            "wbs_node_id": uuid.UUID(a.wbs_node_id),
                            "employee_id": uuid.UUID(a.employee_id) if a.employee_id else None,
                            "contractor_label": a.contractor_label,
                            "start_week": a.start_week,
                            "end_week": a.end_week,
                            "hours_per_week": a.hours_per_week,
                            "baseline_start_week": a.baseline_start_week,
                            "baseline_end_week": a.baseline_end_week,
                            "baseline_employee_id": (
                                uuid.UUID(a.baseline_employee_id)
                                if a.baseline_employee_id
                                else None
                            ),
                        }
                        for a in report.assignments
                    ]
                )
            )
        if report.recommendations:
            await self._conn.execute(
                sa.insert(recommendations).values(
                    [
                        {
                            "run_id": run_id,
                            "type": rec.type,
                            "wbs_node_id": uuid.UUID(rec.wbs_node_id) if rec.wbs_node_id else None,
                            "before_value": rec.before_value,
                            "after_value": rec.after_value,
                            "reason": rec.reason,
                            "sort_order": rec.sort_order,
                        }
                        for rec in report.recommendations
                    ]
                )
            )

    async def fail(self, run_id: uuid.UUID, error: str) -> None:
        await self._conn.execute(
            sa.update(r)
            .where(r.c.id == run_id, r.c.status.in_(ACTIVE))
            .values(status=SolverStatus.FAILED, error=error, finished_at=sa.func.now())
        )

    async def fail_stale(self, older_than_s: int, error: str) -> int:
        result = await self._conn.execute(
            sa.update(r)
            .where(r.c.status.in_(ACTIVE), _older_than(older_than_s))
            .values(status=SolverStatus.FAILED, error=error, finished_at=sa.func.now())
        )
        return int(result.rowcount)
