"""In-process optimization worker (plan.md §0: no Redis yet).

The API commits a `queued` run and then calls `OptimizationRunner.submit`.
The run is solved on a worker thread (`asyncio.to_thread`, the solver is
CPU-bound) and its outcome written on a service connection, which bypasses
RLS: this is a trusted system job, and `planned_assignments` /
`recommendations` have no user write policies.

Railway restarts the process on every deploy, so runs can be orphaned:
`recover_stale` (on startup) and the API (before answering 409) mark runs
stuck in queued/running for longer than `OPTIMIZER_STALE_AFTER_S` as failed.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.optimization.baseline import baseline_plan
from app.optimization.explain import build_report
from app.optimization.model import PlanningInput, SnapshotError, SolveOutcome, build_resources
from app.optimization.solver import SolveControl, SolveOptions, solve
from app.repositories.optimization import OptimizationJobStore

logger = logging.getLogger("app.jobs.optimizer")

RESTART_ERROR = "The server restarted during the run. Please run again."
FAILED_ERROR = "The optimizer hit an unexpected error. Please run again."
SNAPSHOT_ERROR = "The project's planning data couldn't be read. Please run again."
GUROBI_UNAVAILABLE_ERROR = (
    "Gurobi is unavailable or its license is not configured for this environment."
)

StoreFactory = Callable[[], AbstractAsyncContextManager[OptimizationJobStore]]


def gurobi_failure(inp: PlanningInput, message: str) -> SolveOutcome:
    """Preserve solver attribution even when the adapter cannot be loaded."""
    return SolveOutcome(
        status="failed",
        solver="Gurobi",
        solver_version="unavailable",
        wall_seconds=0,
        resources=build_resources(inp),
        baseline=baseline_plan(inp),
        message=message,
    )


def engine_store(engine: AsyncEngine) -> StoreFactory:
    @asynccontextmanager
    async def factory() -> AsyncIterator[OptimizationJobStore]:
        async with engine.begin() as conn:
            yield OptimizationJobStore(conn)

    return factory


class OptimizationRunner:
    def __init__(self, store: StoreFactory | None, settings: Settings) -> None:
        self._store = store
        self._options = SolveOptions(
            time_limit_s=settings.optimizer_time_limit_s, workers=settings.optimizer_workers
        )
        self._stale_after_s = settings.optimizer_stale_after_s
        self._solver_name = settings.optimizer_solver
        # Strong references: the event loop only keeps weak ones to tasks.
        self._tasks: set[asyncio.Task[None]] = set()
        self._controls: dict[uuid.UUID, SolveControl] = {}
        self._closing = False

    @property
    def active(self) -> int:
        return len(self._tasks)

    async def submit(self, run_id: uuid.UUID) -> None:
        """Start solving a committed, queued run in the background.

        Async on purpose: Starlette runs sync background tasks in a thread
        pool, where there is no event loop to schedule the job on.
        """
        if self._store is None or self._closing:
            logger.warning("optimizer unavailable; run left queued", extra={"run_id": str(run_id)})
            return
        task = asyncio.get_running_loop().create_task(self._run(run_id), name=f"opt-{run_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, run_id: uuid.UUID) -> None:
        assert self._store is not None  # noqa: S101 - guarded by submit()
        extra = {"run_id": str(run_id)}
        control = SolveControl()
        self._controls[run_id] = control
        try:
            async with self._store() as store:
                snapshot = await store.claim(run_id)
            if snapshot is None:
                logger.info("optimization run not queued; skipping", extra=extra)
                return
            inp = PlanningInput.from_snapshot(snapshot)
            logger.info(
                "optimization started",
                extra={**extra, "tasks": len(inp.tasks), "employees": len(inp.employees)},
            )
            if self._solver_name == "gurobi":
                try:
                    from app.optimization.gurobi_solver import solve_gurobi
                except ImportError:
                    logger.error("Gurobi optimizer could not be imported")
                    outcome = gurobi_failure(inp, GUROBI_UNAVAILABLE_ERROR)
                else:
                    try:
                        outcome = await asyncio.to_thread(solve_gurobi, inp, self._options, control)
                    except Exception:
                        logger.error("Gurobi optimizer failed", extra=extra)
                        outcome = gurobi_failure(inp, FAILED_ERROR)
            else:
                outcome = await asyncio.to_thread(solve, inp, self._options, control)
            report = build_report(inp, outcome)
            async with self._store() as store:
                await store.finish(run_id, report)
            logger.info(
                "optimization finished",
                extra={**extra, "status": report.status, "runtime_ms": report.runtime_ms},
            )
        except asyncio.CancelledError:
            await self._fail(run_id, RESTART_ERROR)
            raise
        except SnapshotError:
            logger.exception("optimization snapshot invalid", extra=extra)
            await self._fail(run_id, SNAPSHOT_ERROR)
        except Exception:
            logger.exception("optimization run failed", extra=extra)
            await self._fail(run_id, FAILED_ERROR)
        finally:
            self._controls.pop(run_id, None)

    async def _fail(self, run_id: uuid.UUID, error: str) -> None:
        assert self._store is not None  # noqa: S101
        try:
            async with self._store() as store:
                await store.fail(run_id, error)
        except Exception:
            # Startup recovery will mark it failed once it goes stale.
            logger.exception(
                "could not mark optimization run failed", extra={"run_id": str(run_id)}
            )

    async def recover_stale(self) -> int:
        """Fail runs orphaned by a previous process. Never raises."""
        if self._store is None:
            return 0
        try:
            async with self._store() as store:
                count = await store.fail_stale(self._stale_after_s, RESTART_ERROR)
        except Exception:
            logger.exception("optimizer startup recovery failed")
            return 0
        if count:
            logger.warning("marked orphaned optimization runs failed", extra={"count": count})
        return count

    async def shutdown(self, timeout_s: float = 10.0) -> None:
        """Stop solvers, cancel running jobs (they record RESTART_ERROR) and wait."""
        self._closing = True
        for control in list(self._controls.values()):
            control.stop()
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=timeout_s)
