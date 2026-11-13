"""Scope review and AI intake API behavior with fake storage/provider."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import intake, scope
from app.core.config import Settings
from app.core.errors import ServiceUnavailableError
from app.core.security import CurrentPrincipal
from app.domain.common import OrgRole
from app.domain.scope import ScopeItem
from tests.conftest import Store
from tests.test_optimization_api import FakeRuns, RunStore

Auth = Callable[[uuid.UUID], dict[str, str]]


class FakeScope:
    def __init__(self) -> None:
        self.rows: dict[uuid.UUID, ScopeItem] = {}

    async def list(self, project_id: uuid.UUID) -> list[ScopeItem]:
        return [row for row in self.rows.values() if row.project_id == project_id]

    async def get(self, project_id: uuid.UUID, item_id: uuid.UUID) -> ScopeItem | None:
        row = self.rows.get(item_id)
        return row if row and row.project_id == project_id else None

    async def create(self, project_id: uuid.UUID, values: dict[str, Any]) -> uuid.UUID:
        item_id = uuid.uuid4()
        now = datetime.now(UTC)
        self.rows[item_id] = ScopeItem(
            id=item_id,
            project_id=project_id,
            sort_order=0,
            created_at=now,
            updated_at=now,
            **values,
        )
        return item_id

    async def update(
        self, project_id: uuid.UUID, item_id: uuid.UUID, user_id: uuid.UUID, changes: dict[str, Any]
    ) -> bool:
        row = await self.get(project_id, item_id)
        if row is None:
            return False
        self.rows[item_id] = row.model_copy(update=changes)
        return True

    async def delete(self, project_id: uuid.UUID, item_id: uuid.UUID) -> bool:
        if await self.get(project_id, item_id) is None:
            return False
        del self.rows[item_id]
        return True


@pytest.fixture
def scope_client(app: FastAPI, store: Store) -> Iterator[tuple[TestClient, FakeScope]]:
    app.include_router(scope.router, prefix="/api/v1")
    app.include_router(intake.router, prefix="/api/v1")
    repo = FakeScope()

    def access(principal: CurrentPrincipal) -> FakeRuns:
        return FakeRuns(store, RunStore(), principal.user_id)

    app.dependency_overrides[scope.get_project_access] = access
    app.dependency_overrides[scope.get_scope_repo] = lambda: repo
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, repo


def project(client: TestClient, store: Store, auth: Auth) -> tuple[uuid.UUID, uuid.UUID]:
    owner = store.add_user()
    org = store.add_org(owner)
    response = client.post(
        f"/api/v1/orgs/{org}/projects", headers=auth(owner), json={"name": "Demo"}
    )
    assert response.status_code == 201
    return owner, uuid.UUID(response.json()["id"])


def test_manual_scope_review_and_authorization(
    scope_client: tuple[TestClient, FakeScope], store: Store, auth: Auth
) -> None:
    client, repo = scope_client
    owner, project_id = project(client, store, auth)
    base = f"/api/v1/projects/{project_id}/scope-items"
    created = client.post(base, headers=auth(owner), json={"category": "risk", "text": "Delay"})
    assert created.status_code == 201, created.text
    item_id = created.json()["id"]
    assert created.json()["status"] == "pending"
    assert created.json()["origin"] == "manual"
    forged = client.post(
        base, headers=auth(owner), json={"category": "risk", "text": "x", "origin": "ai"}
    )
    assert forged.status_code == 422
    assert client.get(base, headers=auth(owner)).json()[0]["text"] == "Delay"
    updated = client.patch(f"{base}/{item_id}", headers=auth(owner), json={"status": "accepted"})
    assert updated.json()["status"] == "accepted"
    invalid = client.patch(f"{base}/{item_id}", headers=auth(owner), json={"text": ""})
    assert invalid.status_code == 422

    viewer = store.add_user("Viewer")
    store.members[(store.projects[project_id].org_id, viewer)] = OrgRole.VIEWER
    assert client.get(base, headers=auth(viewer)).status_code == 200
    denied = client.post(base, headers=auth(viewer), json={"category": "risk", "text": "x"})
    assert denied.status_code == 403
    outsider = store.add_user("Outsider")
    assert client.get(base, headers=auth(outsider)).status_code == 404
    assert client.delete(f"{base}/{item_id}", headers=auth(owner)).status_code == 204
    assert not repo.rows


def test_intake_calls_authorization_before_provider_and_saves_only_validated_items(
    scope_client: tuple[TestClient, FakeScope],
    store: Store,
    auth: Auth,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, repo = scope_client
    owner, project_id = project(client, store, auth)
    calls: list[str] = []

    async def check(*args: Any) -> None:
        calls.append("auth")

    async def generate(*args: Any) -> intake.Suggestions:
        calls.append("provider")
        return intake.Suggestions(items=[intake.Suggestion(category="risk", text="Schedule risk")])

    async def save(
        request: Any, principal: Any, pid: uuid.UUID, result: intake.Suggestions
    ) -> list[ScopeItem]:
        calls.append("save")
        assert pid == project_id
        item_id = await repo.create(
            pid, {**result.items[0].model_dump(), "origin": "ai", "status": "pending"}
        )
        item = await repo.get(pid, item_id)
        assert item is not None
        return [item]

    monkeypatch.setattr(intake, "verify_access", check)
    monkeypatch.setattr(intake, "generate", generate)
    monkeypatch.setattr(intake, "save_suggestions", save)
    url = f"/api/v1/projects/{project_id}/intake/analyze"
    response = client.post(url, headers=auth(owner), json={"text": "We may be late"})
    assert response.status_code == 200, response.text
    assert calls == ["auth", "provider", "save"]
    assert response.json()["items"][0]["status"] == "pending"
    assert response.json()["items"][0]["origin"] == "ai"

    async def fail(*args: Any) -> intake.Suggestions:
        raise ServiceUnavailableError("AI intake is not configured")

    monkeypatch.setattr(intake, "generate", fail)
    calls.clear()
    response = client.post(url, headers=auth(owner), json={"text": "test"})
    assert response.status_code == 503
    assert calls == ["auth"]
    assert len(repo.rows) == 1


async def test_unconfigured_provider_is_not_fake(settings: Settings) -> None:
    settings.openai_api_key = None
    with pytest.raises(ServiceUnavailableError, match="not configured"):
        await intake.generate(settings, "Project notes")
