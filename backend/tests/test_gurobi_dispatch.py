"""The selected solver is used explicitly, including unavailable-license outcomes."""

from __future__ import annotations

import asyncio
import builtins
import sys
import types
import uuid
from typing import Any

import pytest

from app.core.config import Settings
from app.jobs import optimizer as jobs
from app.optimization.baseline import baseline_plan
from app.optimization.model import PlanningInput, SolveOutcome, build_resources
from app.optimization.solver import SolveControl, SolveOptions
from tests.test_optimization_jobs import FakeJobStore


def gurobi_settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None, optimizer_solver="gurobi", optimizer_time_limit_s=5, optimizer_workers=1
    )


async def drain(runner: jobs.OptimizationRunner) -> None:
    if runner._tasks:
        await asyncio.wait(list(runner._tasks))


async def test_gurobi_dispatch_does_not_fall_back(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[PlanningInput] = []

    def unavailable(
        inp: PlanningInput, options: SolveOptions, control: SolveControl
    ) -> SolveOutcome:
        called.append(inp)
        return SolveOutcome(
            status="failed",
            solver="Gurobi",
            solver_version="unavailable",
            wall_seconds=0,
            resources=build_resources(inp),
            baseline=baseline_plan(inp),
            message="Gurobi is unavailable or its license is not configured for this environment.",
        )

    module = types.ModuleType("app.optimization.gurobi_solver")
    module.solve_gurobi = unavailable  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, module.__name__, module)

    def unexpected_fallback(*args: Any) -> None:
        raise AssertionError("CP-SAT must not run when Gurobi is selected")

    monkeypatch.setattr(jobs, "solve", unexpected_fallback)
    store = FakeJobStore()
    runner = jobs.OptimizationRunner(store, gurobi_settings())  # type: ignore[arg-type]
    run_id = uuid.uuid4()
    await runner.submit(run_id)
    await drain(runner)
    assert len(called) == 1
    assert len(store.finished) == 1
    rid, report = store.finished[0]
    assert rid == run_id
    assert report.status == "failed"
    assert report.error is not None and "license" in report.error
    assert store.failed == []


async def test_missing_gurobi_adapter_fails_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def missing(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "app.optimization.gurobi_solver":
            raise ImportError("simulated missing package")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    store = FakeJobStore()
    runner = jobs.OptimizationRunner(store, gurobi_settings())  # type: ignore[arg-type]
    run_id = uuid.uuid4()
    await runner.submit(run_id)
    await drain(runner)
    assert store.failed == []
    assert len(store.finished) == 1
    rid, report = store.finished[0]
    assert rid == run_id
    assert report.status == "failed"
    assert report.solver == "Gurobi"
    assert report.error == jobs.GUROBI_UNAVAILABLE_ERROR


def test_cpsat_remains_default() -> None:
    assert Settings(_env_file=None).optimizer_solver == "cpsat"  # type: ignore[call-arg]
