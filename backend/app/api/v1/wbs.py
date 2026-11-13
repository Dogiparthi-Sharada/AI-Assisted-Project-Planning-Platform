"""Project-scoped WBS editing endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response, status

from app.api.deps import ProjectId, Projects
from app.core.db import UserDB
from app.core.errors import ForbiddenError, NotFoundError, UnprocessableError
from app.domain.wbs import WbsCreate, WbsNode, WbsUpdate
from app.repositories.wbs import WbsRepository

router = APIRouter(tags=["wbs"])


def get_wbs_repo(db: UserDB) -> WbsRepository:
    return WbsRepository(db)


Wbs = Annotated[WbsRepository, Depends(get_wbs_repo)]
NodeId = Annotated[uuid.UUID, Path(alias="nodeId")]


async def ensure_project(project_id: uuid.UUID, projects: Projects) -> None:
    if await projects.get(project_id) is None:
        raise NotFoundError("Project not found")


async def ensure_owner(wbs: Wbs, project_id: uuid.UUID, owner_id: uuid.UUID | None) -> None:
    if owner_id is not None and not await wbs.valid_owner(project_id, owner_id):
        raise UnprocessableError("ownerEmployeeId must belong to the project's organization")


@router.get("/projects/{projectId}/wbs/nodes", response_model=list[WbsNode])
async def list_nodes(project_id: ProjectId, projects: Projects, wbs: Wbs) -> list[WbsNode]:
    await ensure_project(project_id, projects)
    return await wbs.list(project_id)


@router.post(
    "/projects/{projectId}/wbs/nodes", response_model=WbsNode, status_code=status.HTTP_201_CREATED
)
async def create_node(
    project_id: ProjectId, body: WbsCreate, projects: Projects, wbs: Wbs, response: Response
) -> WbsNode:
    await ensure_project(project_id, projects)
    await ensure_owner(wbs, project_id, body.owner_employee_id)
    if body.parent_id is not None:
        parent = await wbs.get(project_id, body.parent_id)
        if parent is None or parent.level != body.level - 1:
            raise UnprocessableError("parentId must identify a node in this project one level up")
    node_id = await wbs.create(project_id, body.values())
    node = await wbs.get(project_id, node_id)
    if node is None:
        raise ForbiddenError("The WBS node was created but is not visible to you")
    response.headers["Location"] = f"/api/v1/projects/{project_id}/wbs/nodes/{node_id}"
    return node


@router.patch("/projects/{projectId}/wbs/nodes/{nodeId}", response_model=WbsNode)
async def update_node(
    project_id: ProjectId, node_id: NodeId, body: WbsUpdate, projects: Projects, wbs: Wbs
) -> WbsNode:
    await ensure_project(project_id, projects)
    if await wbs.get(project_id, node_id) is None:
        raise NotFoundError("WBS node not found")
    await ensure_owner(wbs, project_id, body.owner_employee_id)
    if not await wbs.update(project_id, node_id, body.changes()):
        raise ForbiddenError("You do not have permission to update this WBS node")
    node = await wbs.get(project_id, node_id)
    if node is None:
        raise NotFoundError("WBS node not found")
    return node


@router.delete("/projects/{projectId}/wbs/nodes/{nodeId}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_node(
    project_id: ProjectId, node_id: NodeId, projects: Projects, wbs: Wbs
) -> Response:
    await ensure_project(project_id, projects)
    if await wbs.get(project_id, node_id) is None:
        raise NotFoundError("WBS node not found")
    if not await wbs.delete(project_id, node_id):
        raise ForbiddenError("You do not have permission to delete this WBS node")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
