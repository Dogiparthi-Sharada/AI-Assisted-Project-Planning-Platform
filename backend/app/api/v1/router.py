from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import dashboard, intake, me, notifications, optimization, orgs, planning_resources, projects, scope, wbs

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(me.router)
api_router.include_router(orgs.router)
api_router.include_router(projects.router)
api_router.include_router(dashboard.router)
api_router.include_router(notifications.router)
api_router.include_router(optimization.router)
api_router.include_router(intake.router)
api_router.include_router(scope.router)
api_router.include_router(wbs.router)
api_router.include_router(planning_resources.router)
