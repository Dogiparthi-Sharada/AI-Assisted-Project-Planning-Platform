# ruff: noqa: PLR0911, PLR0912, PLR0914, PLR0915, PLR1702
"""Gurobi mixed-integer implementation of the existing planning contract.

The inputs and reports are shared with the CP-SAT implementation. A binary
variable represents each (task, resource, start day) choice; daily capacity is
therefore linear even when tasks overlap. Importing this module does not load
Gurobi or initialize a license. The adapter deliberately does not silently
fall back to a different solver.
"""

from __future__ import annotations

import importlib
import logging
import math
import threading
import time
from typing import TYPE_CHECKING, Any

from app.optimization.baseline import dependency_offset
from app.optimization.model import (
    Conflict,
    Plan,
    PlanningInput,
    RunStatus,
    ScheduledTask,
    SolveOutcome,
)
from app.optimization.solver import (
    BUDGET_UNIT_CENTS,
    CHANGE_OWNER_CENTS,
    MAKESPAN_CENTS_PER_DAY,
    SHIFT_CENTS_PER_DAY,
    SKILL_GAP_CENTS_PER_LEVEL,
    SolveOptions,
    prepare,
)

if TYPE_CHECKING:
    from app.optimization.solver import SolveControl

logger = logging.getLogger("app.optimization.gurobi")

# Time-indexed MIPs scale with tasks * eligible resources * horizon. Refuse an
# oversized model before allocating its variables rather than exhaust a worker.
MAX_START_CHOICES = 500_000
MAX_CAPACITY_ROWS = 100_000
MAX_CAPACITY_NONZEROS = 5_000_000
_ENV_INIT_LOCK = threading.Lock()


class _StopProxy:
    """Match SolveControl's CP-SAT stop hook without changing its interface."""

    def __init__(self, model: Any) -> None:
        self.model = model

    def stop_search(self) -> None:
        self.model.terminate()


def solve_gurobi(
    inp: PlanningInput, options: SolveOptions | None = None, control: SolveControl | None = None
) -> SolveOutcome:
    """Solve off the event loop; return a structured failure if unavailable."""
    options = options or SolveOptions()
    began = time.perf_counter()
    p = prepare(inp)
    version = "unknown"

    def outcome(status: RunStatus, **kwargs: Any) -> SolveOutcome:
        return SolveOutcome(
            status=status,
            solver="Gurobi" if version == "unknown" else f"Gurobi {version}",
            solver_version=version,
            wall_seconds=time.perf_counter() - began,
            resources=p.resources,
            baseline=p.baseline,
            **kwargs,
        )

    if control is not None and control.stopped:
        return outcome("failed", message="The optimization was stopped.")
    try:
        gp = importlib.import_module("gurobipy")
        GRB = gp.GRB
    except ImportError:
        logger.warning("gurobipy is not installed")
        return outcome(
            "failed",
            message="Gurobi is unavailable or its license is not configured for this environment.",
        )

    version = ".".join(str(part) for part in gp.gurobi.version())
    count = sum(
        max(0, p.horizon - task.duration_days + 1) * len(p.candidates[task.id])
        for task in p.tasks.values()
    )
    # Every start choice for an employee appears in one capacity row per day
    # of that task. Count these terms before building any model objects.
    employee_candidates = {
        r.id for t in p.tasks.values() for r in p.candidates[t.id] if r.kind == "employee"
    }
    capacity_rows = len(employee_candidates) * p.horizon
    capacity_nonzeros = sum(
        max(0, p.horizon - t.duration_days + 1)
        * t.duration_days
        * sum(r.kind == "employee" for r in p.candidates[t.id])
        for t in p.tasks.values()
    )
    if (
        count > MAX_START_CHOICES
        or capacity_rows > MAX_CAPACITY_ROWS
        or capacity_nonzeros > MAX_CAPACITY_NONZEROS
    ):
        return outcome(
            "failed",
            message=(
                "This planning problem is too large for the current optimizer. Reduce the "
                "planning horizon or number of tasks and resources."
            ),
        )

    model: Any = None
    env: Any = None
    attached = False
    try:
        # Gurobi environments are not thread-safe. Initialize them serially and
        # give each concurrent worker a distinct environment and model.
        with _ENV_INIT_LOCK:
            if control is not None and control.stopped:
                return outcome("failed", message="The optimization was stopped.")
            env = gp.Env(empty=True)
            env.setParam("OutputFlag", 0)
            env.start()
        model = gp.Model("project_planning", env=env)
        model.Params.OutputFlag = 0
        model.Params.TimeLimit = max(options.time_limit_s, 0.01)
        model.Params.Threads = max(options.workers, 1)
        model.Params.Seed = max(options.random_seed, 0)
        # Do not label a nonzero-gap incumbent as optimal merely because the
        # Gurobi default MIPGap was reached (normal numerical tolerances apply).
        model.Params.MIPGap = 0.0
        model.Params.MIPGapAbs = 0.0
        c = inp.constraints
        # Save business descriptions alongside constraints. Only hard
        # constraints are candidates for a user-facing infeasibility report.
        named: list[tuple[Any, Conflict]] = []

        def hard(expression: Any, name: str, conflict: Conflict) -> None:
            named.append((model.addConstr(expression, name=name), conflict))

        # Every candidate is represented for IIS analysis, including an
        # unqualified employee in hard-skill mode; constraints then fix it to 0.
        choices: dict[tuple[str, str, int], Any] = {}
        assignment: dict[tuple[str, str], Any] = {}
        starts: dict[str, Any] = {}
        cost_terms: list[Any] = []
        terms: list[Any] = []
        for ti, task in enumerate(p.tasks.values()):
            if control is not None and control.stopped:
                return outcome("failed", message="The optimization was stopped.")
            for ri, resource in enumerate(p.candidates[task.id]):
                options_for_resource = []
                for day in range(p.horizon - task.duration_days + 1):
                    if day % 256 == 0 and control is not None and control.stopped:
                        return outcome("failed", message="The optimization was stopped.")
                    var = model.addVar(vtype=GRB.BINARY, name=f"z_{ti}_{ri}_{day}")
                    choices[task.id, resource.id, day] = var
                    options_for_resource.append(var)
                x = gp.quicksum(options_for_resource)
                assignment[task.id, resource.id] = x
                gap = resource.skill_gap(task)
                if gap and c.skill_mode == "hard":
                    for si, req in enumerate(task.skills):
                        if resource.proficiency(req.skill_id) < req.min_proficiency:
                            hard(
                                x == 0,
                                f"skill_{ti}_{ri}_{si}",
                                Conflict(
                                    f"Skill: {task.label} requires {req.skill_name} "
                                    f"≥ {req.min_proficiency}",
                                    "This resource does not meet the required skill proficiency.",
                                ),
                            )
                elif gap:
                    terms.append(x * gap * SKILL_GAP_CENTS_PER_LEVEL)
                cost_terms.append(x * p.cost[task.id, resource.id])
            model.addConstr(
                gp.quicksum(assignment[task.id, r.id] for r in p.candidates[task.id]) == 1,
                name=f"assign_{ti}",
            )
            starts[task.id] = gp.quicksum(
                day * choices[task.id, resource.id, day]
                for resource in p.candidates[task.id]
                for day in range(p.horizon - task.duration_days + 1)
            )
            base = p.baseline.tasks[task.id]
            terms.append((starts[task.id] - base.start) * SHIFT_CENTS_PER_DAY)
            # CP-SAT omits the owner-change term if the owner is ineligible.
            owner = next(
                (
                    r
                    for r in p.candidates[task.id]
                    if r.id == base.resource_id
                    and (c.skill_mode != "hard" or not r.skill_gap(task))
                ),
                None,
            )
            if owner is not None:
                terms.append((1 - assignment[task.id, owner.id]) * CHANGE_OWNER_CENTS)

        # Capacity uses tenths of an hour per day and one integer peak slack per
        # employee, matching the CP-SAT peak-overallocation objective.
        for ri, resource in enumerate(p.resources):
            if control is not None and control.stopped:
                return outcome("failed", message="The optimization was stopped.")
            if resource.kind != "employee":
                continue
            tasks = [t for t in p.tasks.values() if (t.id, resource.id) in assignment]
            if not tasks:
                continue
            cap = p.capacity[resource.id]
            slack: Any = 0
            if c.capacity_mode == "soft":
                slack = model.addVar(vtype=GRB.INTEGER, lb=0, name=f"over_capacity_{ri}")
                terms.append(slack * round(c.capacity_penalty * 10))
            for day in range(p.horizon):
                if day % 128 == 0 and control is not None and control.stopped:
                    return outcome("failed", message="The optimization was stopped.")
                existing = sum(
                    b.tenths for b in p.blocks.get(resource.id, []) if b.lo <= day < b.hi
                )
                load = gp.quicksum(
                    p.demand[t.id] * choices[t.id, resource.id, start]
                    for t in tasks
                    for start in range(
                        max(0, day - t.duration_days + 1),
                        min(day, p.horizon - t.duration_days) + 1,
                    )
                )
                expression = load + existing <= cap + slack
                if c.capacity_mode == "hard":
                    hard(
                        expression,
                        f"capacity_{ri}_{day}",
                        Conflict(
                            f"Capacity: {resource.name}",
                            "Available capacity and existing allocations cannot satisfy "
                            "this schedule.",
                        ),
                    )
                else:
                    model.addConstr(expression, name=f"capacity_{ri}_{day}")

        for di, dep in enumerate(p.deps):
            pred, succ = p.tasks[dep.predecessor_id], p.tasks[dep.successor_id]
            offset = dependency_offset(dep, pred.duration_days, succ.duration_days)
            hard(
                starts[succ.id] >= starts[pred.id] + offset,
                f"dependency_{di}",
                Conflict(
                    f"Dependency: {pred.label} → {succ.label}",
                    "This task relationship conflicts with other hard constraints.",
                ),
            )

        makespan = model.addVar(vtype=GRB.INTEGER, lb=0, ub=p.horizon, name="makespan")
        for ti, task in enumerate(p.tasks.values()):
            model.addConstr(makespan >= starts[task.id] + task.duration_days, name=f"finish_{ti}")
        terms.append(makespan * MAKESPAN_CENTS_PER_DAY)
        per_day = round(c.deadline_penalty_per_day * 100)
        if p.deadline is not None:
            if c.deadline_mode == "hard":
                hard(
                    makespan <= p.deadline,
                    "deadline",
                    Conflict(
                        "Deadline",
                        "The project cannot finish by the deadline with the other "
                        "hard constraints.",
                    ),
                )
            else:
                late = model.addVar(vtype=GRB.INTEGER, lb=0, ub=p.horizon, name="late")
                model.addConstr(late >= makespan - p.deadline, name="deadline_soft")
                terms.append(late * per_day)
        for mi, milestone in enumerate(inp.milestones):
            due = p.milestone_ends.get(milestone.id)
            if due is None:
                continue
            tasks = [p.tasks[tid] for tid in milestone.task_ids if tid in p.tasks]
            if milestone.mode == "hard":
                for ti, task in enumerate(tasks):
                    hard(
                        starts[task.id] + task.duration_days <= due,
                        f"milestone_{mi}_{ti}",
                        Conflict(
                            f"Milestone: {milestone.name}",
                            "This milestone cannot be met with the other hard constraints.",
                        ),
                    )
            else:
                late = model.addVar(
                    vtype=GRB.INTEGER, lb=0, ub=p.horizon, name=f"milestone_late_{mi}"
                )
                for ti, task in enumerate(tasks):
                    model.addConstr(
                        late >= starts[task.id] + task.duration_days - due,
                        name=f"milestone_soft_{mi}_{ti}",
                    )
                terms.append(late * per_day)

        cost = gp.quicksum(cost_terms)
        terms.append(cost)
        for label, cap in (("budget", c.budget_total_cents), ("labor", c.labor_cap_cents)):
            if cap is None:
                continue
            if c.budget_mode == "hard":
                hard(
                    cost <= cap,
                    f"{label}_cap",
                    Conflict(
                        f"{label.title()} cap",
                        "This cap conflicts with the other hard constraints.",
                    ),
                )
            else:
                over = model.addVar(vtype=GRB.INTEGER, lb=0, name=f"over_{label}")
                model.addConstr(over * BUDGET_UNIT_CENTS >= cost - cap, name=f"{label}_soft")
                terms.append(over * round(c.budget_penalty * 100))
        model.setObjective(gp.quicksum(terms), GRB.MINIMIZE)

        if control is not None:
            # SolveControl is annotated for CpSolver but only calls stop_search.
            control.attach(_StopProxy(model))  # type: ignore[arg-type]
            attached = True
            if control.stopped:
                return outcome("failed", message="The optimization was stopped.")

        def check_cancel(current_model: Any, _where: int) -> None:
            # Also catches stop requests racing with the start of optimize().
            if control is not None and control.stopped:
                current_model.terminate()

        model.optimize(check_cancel)
        if control is not None and control.stopped:
            return outcome("failed", message="The optimization was stopped.")
        status = model.Status
        if status == GRB.INF_OR_UNBD:
            remaining = options.time_limit_s - (time.perf_counter() - began)
            if remaining <= 0:
                return outcome(
                    "timeout",
                    hit_time_limit=True,
                    message="The optimizer could not determine feasibility within the time limit.",
                )
            model.Params.DualReductions = 0
            model.Params.TimeLimit = remaining
            model.reset()
            model.optimize(check_cancel)
            status = model.Status
            if control is not None and control.stopped:
                return outcome("failed", message="The optimization was stopped.")

        if status == GRB.INFEASIBLE:
            conflicts: list[Conflict] = []
            try:
                model.Params.TimeLimit = max(options.iis_time_limit_s or options.time_limit_s, 0.01)
                model.computeIIS()
                if model.IISMinimal:
                    seen: set[str] = set()
                    for constraint, conflict in named:
                        if constraint.IISConstr and conflict.name not in seen:
                            conflicts.append(conflict)
                            seen.add(conflict.name)
            except gp.GurobiError:
                logger.warning("Gurobi could not complete infeasibility analysis")
            if control is not None and control.stopped:
                return outcome("failed", message="The optimization was stopped.")
            if not conflicts:
                conflicts.append(
                    Conflict(
                        "Hard constraints", "The hard constraints cannot all be satisfied together."
                    )
                )
            return outcome("infeasible", conflicts=tuple(conflicts))

        acceptable = {
            GRB.OPTIMAL, GRB.TIME_LIMIT, GRB.ITERATION_LIMIT, GRB.NODE_LIMIT,
            GRB.SOLUTION_LIMIT, GRB.INTERRUPTED, GRB.SUBOPTIMAL,
        }
        # WORK_LIMIT and MEM_LIMIT are present in recent Gurobi versions.
        acceptable.update(
            getattr(GRB, name)
            for name in ("WORK_LIMIT", "MEM_LIMIT", "USER_OBJ_LIMIT")
            if hasattr(GRB, name)
        )
        if status in acceptable and model.SolCount:
            plan: dict[str, ScheduledTask] = {}
            for task in p.tasks.values():
                chosen = next(
                    (resource.id, day)
                    for resource in p.candidates[task.id]
                    for day in range(p.horizon - task.duration_days + 1)
                    if choices[task.id, resource.id, day].X > 0.5
                )
                resource_id, begin = chosen
                plan[task.id] = ScheduledTask(
                    task.id, begin, begin + task.duration_days, resource_id
                )
            bound = float(model.ObjBound)
            return outcome(
                "optimal" if status == GRB.OPTIMAL else "feasible",
                plan=Plan(plan),
                objective=float(model.ObjVal),
                best_bound=bound if math.isfinite(bound) else None,
                hit_time_limit=status == GRB.TIME_LIMIT,
            )
        if status == GRB.TIME_LIMIT:
            return outcome(
                "timeout",
                hit_time_limit=True,
                message="No feasible plan was found within the time limit.",
            )
        return outcome("failed", message="The optimizer could not determine a valid solution.")
    except gp.GurobiError as exc:
        # Avoid printing exception text: license endpoints or credentials can
        # appear in provider errors. Log the numeric code only.
        logger.error("Gurobi model or license error", extra={"gurobi_error_code": exc.errno})
        return outcome(
            "failed",
            message=(
                "Gurobi is unavailable, its license is not valid for this environment or model, "
                "or the model could not be solved."
            ),
        )
    finally:
        if attached and control is not None:
            control.detach()
        if model is not None:
            model.dispose()
        if env is not None:
            env.dispose()
