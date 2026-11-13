"""Saved scope review items. Authorization is enforced through project RLS."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response, status

from app.api.deps import ProjectId
from app.core.db import UserDB
from app.core.errors import ForbiddenError, NotFoundError
from app.core.security import CurrentPrincipal
from app.domain.scope import ScopeCreate, ScopeItem, ScopeUpdate
from app.repositories.optimization import OptimizationRepository
from app.repositories.scope import ScopeRepository

router = APIRouter(tags=["scope"])
ItemId = Annotated[uuid.UUID, Path(alias="itemId")]


def get_scope_repo(db: UserDB) -> ScopeRepository:
    return ScopeRepository(db)


def get_project_access(db: UserDB) -> OptimizationRepository:
    return OptimizationRepository(db)


Scope = Annotated[ScopeRepository, Depends(get_scope_repo)]
Access = Annotated[OptimizationRepository, Depends(get_project_access)]


async def ensure_project(
    project_id: uuid.UUID, access: OptimizationRepository, *, write: bool = False
) -> None:
    if await access.project(project_id) is None:
        raise NotFoundError("Project not found")
    if write and not await access.can_write(project_id):
        raise ForbiddenError("You do not have permission to change this project")


@router.get("/projects/{projectId}/scope-items", response_model=list[ScopeItem])
async def list_items(project_id: ProjectId, scope: Scope, access: Access) -> list[ScopeItem]:
    await ensure_project(project_id, access)
    return await scope.list(project_id)


@router.post(
    "/projects/{projectId}/scope-items",
    response_model=ScopeItem,
    status_code=status.HTTP_201_CREATED,
)
async def create_item(
    project_id: ProjectId, body: ScopeCreate, scope: Scope, access: Access
) -> ScopeItem:
    await ensure_project(project_id, access, write=True)
    item_id = await scope.create(project_id, {
        **body.model_dump(), "status": "pending", "origin": "manual"
    })
    item = await scope.get(project_id, item_id)
    if item is None:
        raise ForbiddenError("Created item is not visible")
    return item


@router.patch("/projects/{projectId}/scope-items/{itemId}", response_model=ScopeItem)
async def update_item(
    project_id: ProjectId, item_id: ItemId, body: ScopeUpdate, principal: CurrentPrincipal,
    scope: Scope, access: Access,
) -> ScopeItem:
    await ensure_project(project_id, access, write=True)
    if await scope.get(project_id, item_id) is None:
        raise NotFoundError("Scope item not found")
    changes = body.model_dump(exclude_unset=True)
    if not await scope.update(project_id, item_id, principal.user_id, changes):
        raise ForbiddenError("You do not have permission to change this item")
    item = await scope.get(project_id, item_id)
    if item is None:
        raise NotFoundError("Scope item not found")
    return item


@router.delete("/projects/{projectId}/scope-items/{itemId}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_item(
    project_id: ProjectId, item_id: ItemId, scope: Scope, access: Access
) -> Response:
    await ensure_project(project_id, access, write=True)
    if await scope.get(project_id, item_id) is None:
        raise NotFoundError("Scope item not found")
    if not await scope.delete(project_id, item_id):
        raise ForbiddenError("You do not have permission to delete this item")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
