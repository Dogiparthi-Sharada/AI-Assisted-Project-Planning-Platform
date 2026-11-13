"""WBS endpoint contracts with request-scoped repository fakes."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.wbs import get_wbs_repo
from app.core.security import CurrentPrincipal
from app.domain.common import OrgRole
from app.domain.wbs import WbsNode
from tests.conftest import Store, rls_violation


class FakeWbs:
    def __init__(self, store: Store, user_id: uuid.UUID, nodes: dict[uuid.UUID, WbsNode]) -> None:
        self.store, self.user_id, self.nodes = store, user_id, nodes

    def can_write(self, project_id: uuid.UUID) -> bool:
        project = self.store.projects[project_id]
        return self.store.role(project.org_id, self.user_id) not in (None, OrgRole.VIEWER)

    async def list(self, project_id: uuid.UUID) -> list[WbsNode]:
        return [n for n in self.nodes.values() if n.project_id == project_id]

    async def get(self, project_id: uuid.UUID, node_id: uuid.UUID) -> WbsNode | None:
        node = self.nodes.get(node_id)
        return node if node and node.project_id == project_id else None

    async def valid_owner(self, project_id: uuid.UUID, owner_id: uuid.UUID) -> bool:
        return False

    async def create(self, project_id: uuid.UUID, values: dict[str, Any]) -> uuid.UUID:
        if not self.can_write(project_id):
            raise rls_violation()
        node_id = uuid.uuid4()
        self.nodes[node_id] = WbsNode(id=node_id, project_id=project_id, **values)
        return node_id

    async def update(
        self, project_id: uuid.UUID, node_id: uuid.UUID, values: dict[str, Any]
    ) -> bool:
        if not self.can_write(project_id):
            return False
        existing = self.nodes[node_id].model_dump(by_alias=False)
        self.nodes[node_id] = WbsNode.model_validate({**existing, **values})
        return True

    async def delete(self, project_id: uuid.UUID, node_id: uuid.UUID) -> bool:
        if not self.can_write(project_id):
            return False
        descendants = [n.id for n in self.nodes.values() if n.parent_id == node_id]
        for child in descendants:
            await self.delete(project_id, child)
        del self.nodes[node_id]
        return True


@pytest.fixture
def wbs_data(app: FastAPI, store: Store) -> dict[uuid.UUID, WbsNode]:
    nodes: dict[uuid.UUID, WbsNode] = {}

    def repo(principal: CurrentPrincipal) -> FakeWbs:
        return FakeWbs(store, principal.user_id, nodes)

    app.dependency_overrides[get_wbs_repo] = repo
    return nodes


def test_wbs_project_tree_crud(client: TestClient, store: Store, auth: Any, wbs_data: Any) -> None:
    user = store.add_user("Owner")
    org = store.add_org(user, "Team")
    headers = auth(user)
    project = client.post(
        f"/api/v1/orgs/{org}/projects", json={"name": "Plan"}, headers=headers
    ).json()
    base = f"/api/v1/projects/{project['id']}/wbs/nodes"
    root = client.post(base, json={"name": "Plan", "level": 1}, headers=headers)
    assert root.status_code == 201
    root_id = root.json()["id"]
    assert root.headers["location"].endswith(root_id)
    child = client.post(
        base,
        json={"name": "Delivery", "level": 2, "parentId": root_id, "sortOrder": 1},
        headers=headers,
    )
    assert child.status_code == 201
    child_id = child.json()["id"]
    leaf = client.post(
        base,
        json={"name": "Build", "level": 3, "parentId": child_id, "durationDays": 5},
        headers=headers,
    )
    assert leaf.status_code == 201
    leaf_id = leaf.json()["id"]
    listed = client.get(base, headers=headers)
    assert listed.status_code == 200
    assert {n["id"] for n in listed.json()} == {root_id, child_id, leaf_id}
    update = client.patch(
        f"{base}/{leaf_id}",
        json={"name": "Build system", "durationDays": 7, "effortHours": 37.5},
        headers=headers,
    )
    assert update.status_code == 200
    assert update.json()["durationDays"] == 7
    assert update.json()["effortHours"] == 37.5
    assert client.delete(f"{base}/{child_id}", headers=headers).status_code == 204
    assert [n["id"] for n in client.get(base, headers=headers).json()] == [root_id]


def test_wbs_denies_other_tenant_and_viewer_writes(
    client: TestClient, store: Store, auth: Any, wbs_data: Any
) -> None:
    owner, outsider, viewer = (store.add_user(name) for name in ("Owner", "Outsider", "Viewer"))
    org = store.add_org(owner, "Team")
    store.members[(org, viewer)] = OrgRole.VIEWER
    headers = auth(owner)
    p = client.post(f"/api/v1/orgs/{org}/projects", json={"name": "Plan"}, headers=headers).json()
    base = f"/api/v1/projects/{p['id']}/wbs/nodes"
    root = client.post(base, json={"name": "Plan", "level": 1}, headers=headers).json()
    assert client.get(base, headers=auth(outsider)).status_code == 404
    assert client.get(base, headers=auth(viewer)).status_code == 200
    assert (
        client.post(base, json={"name": "X", "level": 1}, headers=auth(viewer)).status_code == 403
    )
    assert client.patch(
        f"{base}/{root['id']}", json={"name": "Changed"}, headers=auth(viewer)
    ).status_code == 403
    assert client.delete(f"{base}/{root['id']}", headers=auth(viewer)).status_code == 403


def test_wbs_rejects_invalid_and_cross_project_references(
    client: TestClient, store: Store, auth: Any, wbs_data: Any
) -> None:
    user = store.add_user("Owner")
    org = store.add_org(user, "Team")
    headers = auth(user)
    p1 = client.post(f"/api/v1/orgs/{org}/projects", json={"name": "P1"}, headers=headers).json()
    p2 = client.post(f"/api/v1/orgs/{org}/projects", json={"name": "P2"}, headers=headers).json()
    base = f"/api/v1/projects/{p1['id']}/wbs/nodes"
    other = f"/api/v1/projects/{p2['id']}/wbs/nodes"
    root = client.post(base, json={"name": "Root", "level": 1}, headers=headers).json()
    assert (
        client.post(
            other, json={"name": "Bad", "level": 2, "parentId": root["id"]}, headers=headers
        ).status_code
        == 422
    )
    assert (
        client.patch(f"{other}/{root['id']}", json={"name": "Bad"}, headers=headers).status_code
        == 404
    )
    assert client.post(base, json={"name": " ", "level": 1}, headers=headers).status_code == 422
    invalid = [
        {"name": "No parent", "level": 2},
        {"name": "X", "level": 1, "durationDays": -1},
        {"name": "X", "level": 1, "effortHours": 1.25},
        {"name": "X", "level": 1, "ownerEmployeeId": str(uuid.uuid4())},
    ]
    for body in invalid:
        assert client.post(base, json=body, headers=headers).status_code == 422
    assert (
        client.patch(f"{base}/{root['id']}", json={"name": None}, headers=headers).status_code
        == 422
    )
    assert (
        client.patch(f"{base}/{root['id']}", json={"parentId": None}, headers=headers).status_code
        == 422
    )
