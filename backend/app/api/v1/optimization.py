from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Request, Response, status

from app.api.deps import OptimizationRuns, Optimizer, ProjectId, RunId
from app.core.errors import ConflictError, ForbiddenError, NotFoundError, UnprocessableError
from app.domain.optimization import OptimizationRun
from app.jobs.optimizer import RESTART_ERROR
from app.optimization.model import PlanningInput
from app.repositories.optimization import ActiveRunExistsError, MissingDurationsError

router = APIRouter(tags=["optimization"])

ACTIVE_RUN_DETAIL = "An optimization is already running for this project."
_MISSING_DETAIL = {
    ("workPackages", "team"): (
        "Add work packages with durations in WBS Builder and team members in Constraints "
        "before running the optimizer."
    ),
    (
        "workPackages",
    ): "Add work packages with durations in WBS Builder before running the optimizer.",
    ("team",): "Add team members in Constraints before running the optimizer.",
}


def missing_inputs(inp: PlanningInput) -> list[str]:
    missing = []
    if not inp.tasks:
        missing.append("workPackages")
    if not inp.employees:
        missing.append("team")
    return missing


@router.post(
    "/projects/{projectId}/optimization/runs",
    response_model=OptimizationRun,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue an optimization run (solved in the background)",
)
async def create_run(
    project_id: ProjectId,
    request: Request,
    response: Response,
    background: BackgroundTasks,
    runs: OptimizationRuns,
    optimizer: Optimizer,
) -> OptimizationRun:
    project = await runs.project(project_id)
    if project is None:
        raise NotFoundError("Project not found")
    if not await runs.can_write(project_id):
        raise ForbiddenError("You do not have permission to optimize this project")

    stale_after_s = request.app.state.settings.optimizer_stale_after_s
    active = await runs.active_run(project_id, stale_after_s)
    if active is not None:
        if not active.stale:
            raise ConflictError(ACTIVE_RUN_DETAIL)
        await runs.fail_run(active.id, RESTART_ERROR)  # orphaned by a restart

    try:
        inp = await runs.load_input(project, datetime.now(UTC).date())
    except MissingDurationsError as exc:
        raise UnprocessableError(
            "Every work package and childless deliverable needs a positive duration before "
            "running the optimizer.",
            extra={"missing": ["workPackages"], "invalidDurations": exc.items},
        ) from exc
    missing = missing_inputs(inp)
    if missing:
        raise UnprocessableError(_MISSING_DETAIL[tuple(missing)], extra={"missing": missing})

    # An unknown rate is not free. If the run uses a budget, require actual rates
    # instead of letting the solver impute an average (or zero) without notice.
    has_budget = (
        inp.constraints.budget_total_cents is not None or inp.constraints.labor_cap_cents is not None
    )
    if has_budget:
        missing_rates = [
            {"id": employee.id, "name": employee.name}
            for employee in inp.employees
            if employee.hourly_rate_cents is None
        ]
        if missing_rates:
            raise UnprocessableError(
                "Enter an hourly cost for each active team member before running "
                "optimization with a total or labor budget.",
                extra={"missing": ["hourlyRates"], "missingHourlyRates": missing_rates},
            )

    try:
        run_id = await runs.create(project_id, inp.to_snapshot())
    except ActiveRunExistsError as exc:  # lost a race with a concurrent request
        raise ConflictError(ACTIVE_RUN_DETAIL) from exc
    run = await runs.get(project_id, run_id)
    if run is None:
        raise ForbiddenError("The run was created but is not visible to you")

    # Background tasks run after the response, i.e. after the transaction has
    # committed, so the worker always finds the queued row.
    background.add_task(optimizer.submit, run_id)
    response.headers["Location"] = f"/api/v1/projects/{project_id}/optimization/runs/{run_id}"
    return run


# Declared before /{runId} so "latest" isn't parsed as a run id.
@router.get(
    "/projects/{projectId}/optimization/runs/latest",
    response_model=OptimizationRun,
    summary="Latest optimization run (404 when the project has none)",
)
async def latest_run(project_id: ProjectId, runs: OptimizationRuns) -> OptimizationRun:
    run = await runs.latest(project_id)
    if run is None:
        raise NotFoundError("No optimization runs yet")
    return run


@router.get(
    "/projects/{projectId}/optimization/runs/{runId}",
    response_model=OptimizationRun,
    summary="Get an optimization run",
)
async def get_run(project_id: ProjectId, run_id: RunId, runs: OptimizationRuns) -> OptimizationRun:
    run = await runs.get(project_id, run_id)
    if run is None:
        raise NotFoundError("Optimization run not found")
    return run
