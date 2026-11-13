"""WBS persistence through the caller's RLS-protected connection."""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain.wbs import WbsNode
from app.repositories.tables import employees, projects

# The database migrations own this table. These columns only describe it.
wbs_nodes = sa.table(
    "wbs_nodes",
    sa.column("id", sa.Uuid),
    sa.column("project_id", sa.Uuid),
    sa.column("parent_id", sa.Uuid),
    sa.column("level", sa.SmallInteger),
    sa.column("sort_order", sa.Integer),
    sa.column("name", sa.Text),
    sa.column("description", sa.Text),
    sa.column("duration_days", sa.Integer),
    sa.column("effort_hours", sa.Numeric(8, 1)),
    sa.column("owner_employee_id", sa.Uuid),
    sa.column("created_at", sa.DateTime(timezone=True)),
    schema="public",
)


class WbsRepository:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def list(self, project_id: uuid.UUID) -> list[WbsNode]:
        stmt = (
            sa.select(wbs_nodes)
            .where(wbs_nodes.c.project_id == project_id)
            .order_by(
                wbs_nodes.c.level, wbs_nodes.c.sort_order, wbs_nodes.c.created_at, wbs_nodes.c.id
            )
        )
        return [WbsNode.model_validate(dict(r._mapping)) for r in (await self._conn.execute(stmt))]

    async def get(self, project_id: uuid.UUID, node_id: uuid.UUID) -> WbsNode | None:
        stmt = sa.select(wbs_nodes).where(
            wbs_nodes.c.project_id == project_id, wbs_nodes.c.id == node_id
        )
        row = (await self._conn.execute(stmt)).first()
        return WbsNode.model_validate(dict(row._mapping)) if row else None

    async def valid_owner(self, project_id: uuid.UUID, owner_id: uuid.UUID) -> bool:
        stmt = (
            sa.select(employees.c.id)
            .join(projects, projects.c.org_id == employees.c.org_id)
            .where(projects.c.id == project_id, employees.c.id == owner_id)
        )
        return (await self._conn.execute(stmt)).first() is not None

    async def create(self, project_id: uuid.UUID, values: dict[str, Any]) -> uuid.UUID:
        node_id = uuid.uuid4()
        stmt = sa.insert(wbs_nodes).values(id=node_id, project_id=project_id, **values)
        await self._conn.execute(stmt)
        return node_id

    async def update(
        self, project_id: uuid.UUID, node_id: uuid.UUID, values: dict[str, Any]
    ) -> bool:
        stmt = (
            sa.update(wbs_nodes)
            .where(wbs_nodes.c.project_id == project_id, wbs_nodes.c.id == node_id)
            .values(**values)
        )
        return (await self._conn.execute(stmt)).rowcount > 0

    async def delete(self, project_id: uuid.UUID, node_id: uuid.UUID) -> bool:
        stmt = sa.delete(wbs_nodes).where(
            wbs_nodes.c.project_id == project_id, wbs_nodes.c.id == node_id
        )
        return (await self._conn.execute(stmt)).rowcount > 0
