"""Offline adapter guard-path checks; no actual Gurobi optimization is run.

Run from backend: python -m unittest discover -s tests/offline -v
"""
from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

from app.optimization.baseline import baseline_plan
from app.optimization.model import Employee, PlanningInput, Task, build_resources


@dataclass
class Options:
    time_limit_s: float = 20.0
    workers: int = 1
    iis_time_limit_s: float | None = None
    random_seed: int = 0


def load_adapter() -> types.ModuleType:
    """Load an isolated copy using a stub for its OR-Tools-dependent imports."""
    stub = types.ModuleType("app.optimization.solver")
    stub.BUDGET_UNIT_CENTS = 100_000
    stub.CHANGE_OWNER_CENTS = 50_000
    stub.MAKESPAN_CENTS_PER_DAY = 20_000
    stub.SHIFT_CENTS_PER_DAY = 1_000
    stub.SKILL_GAP_CENTS_PER_LEVEL = 200_000
    stub.SolveOptions = Options
    stub.prepare = lambda _inp: None
    path = Path(__file__).resolve().parents[2] / "app" / "optimization" / "gurobi_solver.py"
    spec = importlib.util.spec_from_file_location("_gurobi_safety_offline", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"app.optimization.solver": stub}):
        spec.loader.exec_module(module)
    return module


def sample(horizon: int = 5) -> tuple[PlanningInput, types.SimpleNamespace]:
    inp = PlanningInput(
        project_id="p", project_name="Test", start_date=date(2026, 10, 5),
        tasks=(Task("t", "1.1", "Task", 1),),
        employees=(Employee("e", "Person", hourly_rate_cents=1000),),
    )
    resource = build_resources(inp)[0]
    problem = types.SimpleNamespace(
        tasks={"t": inp.tasks[0]}, candidates={"t": [resource]}, horizon=horizon,
        resources=(resource,), baseline=baseline_plan(inp),
    )
    return inp, problem


class FakeLicenseError(Exception):
    errno = 10009


class GurobiOfflineGuards(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = load_adapter()
        self.inp, self.problem = sample()
        self.adapter.prepare = lambda _inp: self.problem

    def test_missing_package_fails_without_fake_success(self) -> None:
        with patch.object(self.adapter.importlib, "import_module", side_effect=ImportError("missing")):
            result = self.adapter.solve_gurobi(self.inp)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.solver, "Gurobi")
        self.assertIsNone(result.plan)
        self.assertIn("unavailable", result.message or "")

    def test_precancelled_run_does_not_import_or_start_gurobi(self) -> None:
        control = types.SimpleNamespace(stopped=True)
        with patch.object(self.adapter.importlib, "import_module") as importer:
            result = self.adapter.solve_gurobi(self.inp, control=control)
        importer.assert_not_called()
        self.assertEqual(result.status, "failed")
        self.assertIn("stopped", result.message or "")

    def test_license_failure_is_sanitized_and_disposes_environment(self) -> None:
        env = Mock()
        env.start.side_effect = FakeLicenseError("private-license-information")
        gp = types.SimpleNamespace(
            gurobi=types.SimpleNamespace(version=lambda: (12, 0, 0)),
            GRB=types.SimpleNamespace(), GurobiError=FakeLicenseError,
            Env=Mock(return_value=env), Model=Mock(),
        )
        with patch.object(self.adapter.importlib, "import_module", return_value=gp):
            with self.assertLogs("app.optimization.gurobi", level="ERROR") as logs:
                result = self.adapter.solve_gurobi(self.inp)
        self.assertEqual(result.status, "failed")
        self.assertIsNone(result.plan)
        self.assertNotIn("private-license-information", result.message or "")
        self.assertNotIn("private-license-information", " ".join(logs.output))
        gp.Model.assert_not_called()
        env.dispose.assert_called_once_with()

    def test_large_capacity_model_rejected_before_license_initialization(self) -> None:
        self.inp, self.problem = sample(horizon=100_001)
        gp = types.SimpleNamespace(
            gurobi=types.SimpleNamespace(version=lambda: (12, 0, 0)),
            GRB=types.SimpleNamespace(), GurobiError=FakeLicenseError,
            Env=Mock(), Model=Mock(),
        )
        with patch.object(self.adapter.importlib, "import_module", return_value=gp):
            result = self.adapter.solve_gurobi(self.inp)
        self.assertEqual(result.status, "failed")
        self.assertIn("too large", result.message or "")
        self.assertIsNone(result.plan)
        gp.Env.assert_not_called()


if __name__ == "__main__":
    unittest.main()
