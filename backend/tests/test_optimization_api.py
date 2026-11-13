"""Optimization endpoints with real JWT verification and in-memory fakes."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps
from app.core.security import CurrentPrincipal
from app.domain.common import OrgRole, SolverStatus
from app.domain.optimization import OptimizationRun
from app.jobs.optimizer import RESTART_ERROR
from app.optimization.model import Constraints, Employee, PlanningInput, Task
from app.repositories.optimization import (
    ActiveRun,
    ActiveRunExistsError,
    MissingDurationsError,
    ProjectContext,
)
from tests.conftest import Store, rls_violation

Auth = Callable[[uuid.UUID], dict[str, str]]


@dataclass
class RunStore:
    runs: dict[uuid.UUID, dict[str, Any]] = field(default_factory=dict)
    inputs: dict[uuid.UUID, PlanningInput] = field(default_factory=dict)
    race: bool = False  # simulate a concurrent insert hitting the unique index
    invalid_durations: list[dict[str, str]] = field(default_factory=list)


class FakeRuns:
    def __init__(self, store: Store, runs: RunStore, user_id: uuid.UUID) -> None:
        self.s, self.r, self.uid = store, runs, user_id

    def _role(self, project_id: uuid.UUID) -> OrgRole | None:
        p = self.s.projects.get(project_id)
        return self.s.role(p.org_id, self.uid) if p else None

    async def project(self, project_id: uuid.UUID) -> ProjectContext | None:
        p = self.s.projects.get(project_id)
        if p is None or self._role(project_id) is None:
            return None
        return ProjectContext(p.id, p.org_id, p.name, p.start_date, p.target_end_date, None)

    async def can_write(self, project_id: uuid.UUID) -> bool:
        return self._role(project_id) not in (None, OrgRole.VIEWER)

    async def active_run(self, project_id: uuid.UUID, stale_after_s: int) -> ActiveRun | None:
        cutoff = datetime.now(UTC) - timedelta(seconds=stale_after_s)
        for run in self.r.runs.values():
            if run["project_id"] == project_id and run["status"] in ("queued", "running"):
                return ActiveRun(run["id"], run["created_at"] < cutoff)
        return None

    async def fail_run(self, run_id: uuid.UUID, error: str) -> None:
        self.r.runs[run_id].update(status="failed", error=error)

    async def load_input(self, project: ProjectContext, today: date) -> PlanningInput:
        if self.r.invalid_durations:
            raise MissingDurationsError(self.r.invalid_durations)
        return self.r.inputs.get(
            project.id, PlanningInput(str(project.id), project.name, today, (), ())
        )

    async def create(self, project_id: uuid.UUID, snapshot: dict[str, Any]) -> uuid.UUID:
        if not await self.can_write(project_id):
            raise rls_violation()
        if self.r.race:
            raise ActiveRunExistsError
        rid = uuid.uuid4()
        self.r.runs[rid] = {
            "id": rid,
            "project_id": project_id,
            "status": "queued",
            "solver": None,
            "solve_seconds": None,
            "error": None,
            "iis": None,
            "result": None,
            "created_at": datetime.now(UTC),
            "started_at": None,
            "finished_at": None,
            "snapshot": snapshot,
        }
        return rid

    def _visible(self, run: dict[str, Any]) -> OptimizationRun | None:
        if self._role(run["project_id"]) is None:
            return None
        return OptimizationRun.model_validate({k: v for k, v in run.items() if k != "snapshot"})

    async def get(self, project_id: uuid.UUID, run_id: uuid.UUID) -> OptimizationRun | None:
        run = self.r.runs.get(run_id)
        return self._visible(run) if run and run["project_id"] == project_id else None

    async def latest(self, project_id: uuid.UUID) -> OptimizationRun | None:
        rows = [r for r in self.r.runs.values() if r["project_id"] == project_id]
        if not rows:
            return None
        return self._visible(max(rows, key=lambda r: r["created_at"]))


class FakeRunner:
    def __init__(self) -> None:
        self.submitted: list[uuid.UUID] = []

    async def submit(self, run_id: uuid.UUID) -> None:
        self.submitted.append(run_id)


@pytest.fixture
def run_store() -> RunStore:
    return RunStore()


@pytest.fixture
def runner() -> FakeRunner:
    return FakeRunner()


@pytest.fixture
def opt_client(
    app: FastAPI, store: Store, run_store: RunStore, runner: FakeRunner
) -> Iterator[TestClient]:
    def runs(principal: CurrentPrincipal) -> FakeRuns:
        return FakeRuns(store, run_store, principal.user_id)

    app.dependency_overrides[deps.get_optimization_repo] = runs
    app.dependency_overrides[deps.get_optimization_runner] = lambda: runner
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _project(client: TestClient, store: Store, auth: Auth) -> tuple[uuid.UUID, uuid.UUID, str]:
    user = store.add_user("Jane Doe")
    org = store.add_org(user, "Acme")
    r = client.post(f"/api/v1/orgs/{org}/projects", json={"name": "CRM"}, headers=auth(user))
    return user, org, r.json()["id"]


def _ready(run_store: RunStore, project_id: str) -> None:
    pid = uuid.UUID(project_id)
    run_store.inputs[pid] = PlanningInput(
        project_id=project_id,
        project_name="CRM",
        start_date=date(2026, 10, 5),
        tasks=(Task("t1", "1.1.1", "Design", 5),),
        employees=(Employee("e1", "Mark Chen"),),
    )


def _url(project_id: str, suffix: str = "") -> str:
    return f"/api/v1/projects/{project_id}/optimization/runs{suffix}"


RUN_KEYS = {
    "id",
    "projectId",
    "status",
    "solver",
    "solveSeconds",
    "error",
    "iis",
    "result",
    "createdAt",
    "startedAt",
    "finishedAt",
}


def test_create_run_is_202_and_submits(
    opt_client: TestClient, store: Store, run_store: RunStore, runner: FakeRunner, auth: Auth
) -> None:
    user, _, pid = _project(opt_client, store, auth)
    _ready(run_store, pid)
    r = opt_client.post(_url(pid), headers=auth(user))
    assert r.status_code == 202, r.text
    body = r.json()
    assert set(body) == RUN_KEYS
    assert body["status"] == "queued"
    assert body["projectId"] == pid
    assert body["result"] is None
    assert r.headers["location"] == _url(pid, f"/{body['id']}")
    assert runner.submitted == [uuid.UUID(body["id"])]
    snapshot = run_store.runs[uuid.UUID(body["id"])]["snapshot"]
    assert snapshot["version"] == 1
    assert snapshot["tasks"][0]["code"] == "1.1.1"


def test_create_run_conflict_when_active(
    opt_client: TestClient, store: Store, run_store: RunStore, runner: FakeRunner, auth: Auth
) -> None:
    user, _, pid = _project(opt_client, store, auth)
    _ready(run_store, pid)
    assert opt_client.post(_url(pid), headers=auth(user)).status_code == 202
    r = opt_client.post(_url(pid), headers=auth(user))
    assert r.status_code == 409
    assert r.headers["content-type"] == "application/problem+json"
    assert r.json()["detail"] == "An optimization is already running for this project."
    assert len(runner.submitted) == 1


def test_create_run_conflict_on_race(
    opt_client: TestClient, store: Store, run_store: RunStore, auth: Auth
) -> None:
    user, _, pid = _project(opt_client, store, auth)
    _ready(run_store, pid)
    run_store.race = True
    r = opt_client.post(_url(pid), headers=auth(user))
    assert r.status_code == 409
    assert r.json()["detail"] == "An optimization is already running for this project."


def test_stale_active_run_is_failed_and_replaced(
    opt_client: TestClient, store: Store, run_store: RunStore, auth: Auth
) -> None:
    user, _, pid = _project(opt_client, store, auth)
    _ready(run_store, pid)
    first = opt_client.post(_url(pid), headers=auth(user)).json()["id"]
    run_store.runs[uuid.UUID(first)]["created_at"] -= timedelta(hours=1)
    r = opt_client.post(_url(pid), headers=auth(user))
    assert r.status_code == 202
    old = run_store.runs[uuid.UUID(first)]
    assert (old["status"], old["error"]) == ("failed", RESTART_ERROR)


@pytest.mark.parametrize(
    ("tasks", "team", "missing", "detail"),
    [
        (False, False, ["workPackages", "team"], "WBS Builder and team members in Constraints"),
        (False, True, ["workPackages"], "Add work packages with durations in WBS Builder before"),
        (True, False, ["team"], "Add team members in Constraints before"),
    ],
)
def test_create_run_422_lists_missing_inputs(
    opt_client: TestClient,
    store: Store,
    run_store: RunStore,
    runner: FakeRunner,
    auth: Auth,
    tasks: bool,
    team: bool,
    missing: list[str],
    detail: str,
) -> None:
    user, _, pid = _project(opt_client, store, auth)
    run_store.inputs[uuid.UUID(pid)] = PlanningInput(
        pid,
        "CRM",
        date(2026, 10, 5),
        (Task("t1", "1.1", "A", 1),) if tasks else (),
        (Employee("e1", "A"),) if team else (),
    )
    r = opt_client.post(_url(pid), headers=auth(user))
    assert r.status_code == 422
    body = r.json()
    assert body["missing"] == missing
    assert detail in body["detail"]
    assert body["detail"].endswith("before running the optimizer.")
    assert runner.submitted == []
    assert run_store.runs == {}


def test_missing_duration_blocks_run_with_item_details(
    opt_client: TestClient, store: Store, run_store: RunStore, runner: FakeRunner, auth: Auth
) -> None:
    user, _, pid = _project(opt_client, store, auth)
    _ready(run_store, pid)  # another valid task and a team cannot make a partial plan acceptable
    run_store.invalid_durations = [{"id": str(uuid.uuid4()), "code": "1.1.2", "name": "Build"}]
    response = opt_client.post(_url(pid), headers=auth(user))
    assert response.status_code == 422
    assert response.json()["invalidDurations"] == run_store.invalid_durations
    assert response.json()["missing"] == ["workPackages"]
    assert "positive duration" in response.json()["detail"]
    assert runner.submitted == []
    assert run_store.runs == {}


@pytest.mark.parametrize("budget_field", ["budget_total_cents", "labor_cap_cents"])
def test_budget_requires_rates_for_every_active_team_member(
    opt_client: TestClient,
    store: Store,
    run_store: RunStore,
    runner: FakeRunner,
    auth: Auth,
    budget_field: str,
) -> None:
    user, _, pid = _project(opt_client, store, auth)
    run_store.inputs[uuid.UUID(pid)] = PlanningInput(
        pid,
        "CRM",
        date(2026, 10, 5),
        (Task("t1", "1.1", "Design", 5),),
        (Employee("e1", "Known", hourly_rate_cents=10000), Employee("e2", "Missing")),
        constraints=Constraints(**{budget_field: 100000}),  # type: ignore[arg-type]
    )
    response = opt_client.post(_url(pid), headers=auth(user))
    assert response.status_code == 422
    assert response.json()["missingHourlyRates"] == [{"id": "e2", "name": "Missing"}]
    assert response.json()["missing"] == ["hourlyRates"]
    assert runner.submitted == []
    assert run_store.runs == {}


def test_known_zero_rate_counts_as_explicit_for_budget(
    opt_client: TestClient, store: Store, run_store: RunStore, runner: FakeRunner, auth: Auth
) -> None:
    user, _, pid = _project(opt_client, store, auth)
    run_store.inputs[uuid.UUID(pid)] = PlanningInput(
        pid,
        "CRM",
        date(2026, 10, 5),
        (Task("t1", "1.1", "Design", 5),),
        (Employee("e1", "Zero cost", hourly_rate_cents=0),),
        constraints=Constraints(budget_total_cents=100000),
    )
    response = opt_client.post(_url(pid), headers=auth(user))
    assert response.status_code == 202, response.text
    assert len(runner.submitted) == 1


def test_missing_rate_allowed_without_budget(
    opt_client: TestClient, store: Store, run_store: RunStore, runner: FakeRunner, auth: Auth
) -> None:
    user, _, pid = _project(opt_client, store, auth)
    _ready(run_store, pid)
    response = opt_client.post(_url(pid), headers=auth(user))
    assert response.status_code == 202, response.text
    assert len(runner.submitted) == 1


def test_create_run_permissions(
    opt_client: TestClient, store: Store, run_store: RunStore, auth: Auth
) -> None:
    owner, org, pid = _project(opt_client, store, auth)
    _ready(run_store, pid)
    viewer = store.add_user("Vic Viewer")
    store.members[(org, viewer)] = OrgRole.VIEWER
    r = opt_client.post(_url(pid), headers=auth(viewer))
    assert r.status_code == 403
    assert r.headers["content-type"] == "application/problem+json"

    outsider = store.add_user("Eve Outsider")
    assert opt_client.post(_url(pid), headers=auth(outsider)).status_code == 404
    assert opt_client.post(_url(str(uuid.uuid4())), headers=auth(owner)).status_code == 404
    assert opt_client.post(_url(pid)).status_code == 401


def test_latest_and_get(
    opt_client: TestClient, store: Store, run_store: RunStore, auth: Auth
) -> None:
    user, org, pid = _project(opt_client, store, auth)
    r = opt_client.get(_url(pid, "/latest"), headers=auth(user))
    assert r.status_code == 404
    assert r.headers["content-type"] == "application/problem+json"

    _ready(run_store, pid)
    run_id = opt_client.post(_url(pid), headers=auth(user)).json()["id"]
    run = run_store.runs[uuid.UUID(run_id)]
    run.update(
        status=SolverStatus.INFEASIBLE,
        solver="OR-Tools CP-SAT 9.15.6755",
        solve_seconds=1.234,
        iis=[{"name": "Budget cap: $1", "explanation": "Too low."}],
        finished_at=datetime.now(UTC),
    )
    r = opt_client.get(_url(pid, "/latest"), headers=auth(user))
    assert r.status_code == 200
    body = r.json()
    assert set(body) == RUN_KEYS
    assert body["id"] == run_id
    assert body["status"] == "infeasible"
    assert body["solveSeconds"] == 1.234
    assert body["iis"] == [{"name": "Budget cap: $1", "explanation": "Too low."}]

    assert opt_client.get(_url(pid, f"/{run_id}"), headers=auth(user)).status_code == 200
    assert opt_client.get(_url(pid, f"/{uuid.uuid4()}"), headers=auth(user)).status_code == 404
    assert opt_client.get(_url(pid, "/not-a-uuid"), headers=auth(user)).status_code == 422

    viewer = store.add_user("Vic Viewer")
    store.members[(org, viewer)] = OrgRole.VIEWER
    assert opt_client.get(_url(pid, "/latest"), headers=auth(viewer)).status_code == 200
    outsider = store.add_user("Eve")
    assert opt_client.get(_url(pid, "/latest"), headers=auth(outsider)).status_code == 404
    assert opt_client.get(_url(pid, f"/{run_id}"), headers=auth(outsider)).status_code == 404


def test_finished_run_result_is_camel_case(
    opt_client: TestClient, store: Store, run_store: RunStore, auth: Auth
) -> None:
    user, _, pid = _project(opt_client, store, auth)
    _ready(run_store, pid)
    run_id = opt_client.post(_url(pid), headers=auth(user)).json()["id"]
    run_store.runs[uuid.UUID(run_id)].update(
        status="optimal",
        result={
            "kpis": [
                {"label": "Budget", "value": "$1", "sub": "No budget set"},
                {"label": "Solve", "value": "x"},
            ],
            "reassignments": [],
            "scheduleChanges": [
                {
                    "wbs": "1.1.1",
                    "name": "Design",
                    "original": "Wk 1",
                    "optimized": "Wk 1",
                    "delta": "—",
                    "reason": "No change needed",
                }
            ],
            "utilization": [{"name": "Mark Chen", "before": 50, "after": 40}],
        },
    )
    body = opt_client.get(_url(pid, f"/{run_id}"), headers=auth(user)).json()
    assert set(body["result"]) == {"kpis", "reassignments", "scheduleChanges", "utilization"}
    assert body["result"]["kpis"][1] == {"label": "Solve", "value": "x"}  # no null `sub`
    assert body["result"]["scheduleChanges"][0]["delta"] == "—"


def test_optimizer_unavailable_is_503() -> None:
    from types import SimpleNamespace

    from app.core.errors import ServiceUnavailableError

    request: Any = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    with pytest.raises(ServiceUnavailableError):
        deps.get_optimization_runner(request)


def test_openapi_lists_routes(opt_client: TestClient) -> None:
    paths = opt_client.get("/api/openapi.json").json()["paths"]
    assert "/api/v1/projects/{projectId}/optimization/runs" in paths
    assert "/api/v1/projects/{projectId}/optimization/runs/latest" in paths
    assert "/api/v1/projects/{projectId}/optimization/runs/{runId}" in paths
