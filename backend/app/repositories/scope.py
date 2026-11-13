"""Scope storage: all operations use the caller's RLS-scoped connection."""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM, UUID
from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain.scope import ScopeItem

_metadata = sa.MetaData(schema="public")
_scope = sa.Table(
    "scope_items", _metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("project_id", UUID(as_uuid=True), nullable=False),
    sa.Column(
        "category",
        ENUM(
            "objective", "requirement", "deliverable", "constraint", "risk", "missing_info",
            name="scope_category", schema="public", create_type=False,
        ),
    ),
    sa.Column("text", sa.Text),
    sa.Column(
        "status",
        ENUM(
            "pending", "accepted", "removed", name="scope_status", schema="public", create_type=False
        ),
    ),
    sa.Column(
        "origin",
        ENUM("manual", "ai", name="item_origin", schema="public", create_type=False),
    ),
    sa.Column("original_text", sa.Text),
    sa.Column("sort_order", sa.Integer),
    sa.Column("updated_by", UUID(as_uuid=True)),
    sa.Column("created_at", sa.DateTime(timezone=True)),
    sa.Column("updated_at", sa.DateTime(timezone=True)),
)


class ScopeRepository:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def list(self, project_id: uuid.UUID) -> list[ScopeItem]:
        stmt = sa.select(_scope).where(_scope.c.project_id == project_id).order_by(
            _scope.c.sort_order, _scope.c.created_at, _scope.c.id
        )
        rows = (await self._conn.execute(stmt)).all()
        return [ScopeItem.model_validate(dict(row._mapping)) for row in rows]

    async def get(self, project_id: uuid.UUID, item_id: uuid.UUID) -> ScopeItem | None:
        stmt = sa.select(_scope).where(_scope.c.project_id == project_id, _scope.c.id == item_id)
        row = (await self._conn.execute(stmt)).first()
        return ScopeItem.model_validate(dict(row._mapping)) if row else None

    async def create(self, project_id: uuid.UUID, values: dict[str, Any]) -> uuid.UUID:
        item_id = uuid.uuid4()
        original = values["text"] if values["origin"] == "ai" else None
        await self._conn.execute(sa.insert(_scope).values(
            id=item_id, project_id=project_id, original_text=original, **values
        ))
        return item_id

    async def update(
        self, project_id: uuid.UUID, item_id: uuid.UUID, user_id: uuid.UUID, changes: dict[str, Any]
    ) -> bool:
        stmt = sa.update(_scope).where(
            _scope.c.project_id == project_id, _scope.c.id == item_id
        ).values(updated_by=user_id, **changes)
        return (await self._conn.execute(stmt)).rowcount > 0

    async def delete(self, project_id: uuid.UUID, item_id: uuid.UUID) -> bool:
        stmt = sa.delete(_scope).where(_scope.c.project_id == project_id, _scope.c.id == item_id)
        return (await self._conn.execute(stmt)).rowcount > 0
