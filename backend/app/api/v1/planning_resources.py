"""Resource and planning data endpoints, using the caller's RLS session."""
from __future__ import annotations

import uuid
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Path, Response, status
from sqlalchemy.dialects.postgresql import insert

from app.api.deps import OrgId, Orgs, ProjectId, Projects
from app.api.v1.projects import ensure_org_visible
from app.core.db import UserDB
from app.core.errors import ForbiddenError, NotFoundError
from app.domain.projects import Project
from app.domain.planning_resources import (
    ConstraintInput, Constraints, Dependency, DependencyCreate, Employee,
    EmployeeCreate, EmployeeUpdate, EmployeeSkill, EmployeeAllocation, Milestone, MilestoneCreate, Skill, SkillAssignment, SkillCreate,
)
from app.repositories.tables import (
    allocations, constraint_sets, dependencies, employee_skills, employees, milestones, skills,
    task_skill_requirements, wbs_nodes_with_code,
)

router = APIRouter(tags=['planning'])
EmployeeId = Annotated[uuid.UUID, Path(alias='employeeId')]
SkillId = Annotated[uuid.UUID, Path(alias='skillId')]
NodeId = Annotated[uuid.UUID, Path(alias='nodeId')]
DependencyId = Annotated[uuid.UUID, Path(alias='dependencyId')]
MilestoneId = Annotated[uuid.UUID, Path(alias='milestoneId')]


async def _project(project_id: uuid.UUID, projects: Projects) -> Project:
    project = await projects.get(project_id)
    if project is None:
        raise NotFoundError('Project not found')
    return project


async def _employee(db: UserDB, org_id: uuid.UUID, employee_id: uuid.UUID) -> sa.Row[Any]:
    row = (await db.execute(sa.select(employees).where(
        employees.c.org_id == org_id, employees.c.id == employee_id
    ))).first()
    if row is None:
        raise NotFoundError('Employee not found')
    return row


async def _skill(db: UserDB, org_id: uuid.UUID, skill_id: uuid.UUID) -> sa.Row[Any]:
    row = (await db.execute(sa.select(skills).where(
        skills.c.org_id == org_id, skills.c.id == skill_id
    ))).first()
    if row is None:
        raise NotFoundError('Skill not found')
    return row


@router.get('/orgs/{orgId}/employees', response_model=list[Employee])
async def list_employees(org_id: OrgId, orgs: Orgs, db: UserDB) -> list[Employee]:
    await ensure_org_visible(orgs, org_id)
    rows = (await db.execute(sa.select(employees).where(
        employees.c.org_id == org_id, employees.c.active.is_(True)
    ).order_by(employees.c.full_name, employees.c.id))).all()
    return [Employee.model_validate(dict(row._mapping)) for row in rows]


@router.post('/orgs/{orgId}/employees', response_model=Employee, status_code=status.HTTP_201_CREATED)
async def create_employee(org_id: OrgId, body: EmployeeCreate, orgs: Orgs, db: UserDB) -> Employee:
    await ensure_org_visible(orgs, org_id)
    item_id = uuid.uuid4()
    await db.execute(sa.insert(employees).values(id=item_id, org_id=org_id, **body.model_dump(by_alias=False)))
    row = await _employee(db, org_id, item_id)
    return Employee.model_validate(dict(row._mapping))


@router.patch('/orgs/{orgId}/employees/{employeeId}', response_model=Employee)
async def update_employee(org_id: OrgId, employee_id: EmployeeId, body: EmployeeUpdate,
                          orgs: Orgs, db: UserDB) -> Employee:
    await ensure_org_visible(orgs, org_id)
    await _employee(db, org_id, employee_id)
    changes = body.model_dump(exclude_unset=True, by_alias=False)
    result = await db.execute(sa.update(employees).where(
        employees.c.org_id == org_id, employees.c.id == employee_id
    ).values(**changes))
    if result.rowcount == 0:
        raise ForbiddenError('You do not have permission to update this employee')
    row = await _employee(db, org_id, employee_id)
    return Employee.model_validate(dict(row._mapping))


@router.get('/orgs/{orgId}/skills', response_model=list[Skill])
async def list_skills(org_id: OrgId, orgs: Orgs, db: UserDB) -> list[Skill]:
    await ensure_org_visible(orgs, org_id)
    rows = (await db.execute(sa.select(skills).where(skills.c.org_id == org_id)
                             .order_by(skills.c.name, skills.c.id))).all()
    return [Skill.model_validate(dict(row._mapping)) for row in rows]


@router.post('/orgs/{orgId}/skills', response_model=Skill, status_code=status.HTTP_201_CREATED)
async def create_skill(org_id: OrgId, body: SkillCreate, orgs: Orgs, db: UserDB) -> Skill:
    await ensure_org_visible(orgs, org_id)
    item_id = uuid.uuid4()
    await db.execute(sa.insert(skills).values(id=item_id, org_id=org_id, name=body.name))
    row = await _skill(db, org_id, item_id)
    return Skill.model_validate(dict(row._mapping))


@router.put('/orgs/{orgId}/employees/{employeeId}/skills/{skillId}', status_code=204)
async def set_employee_skill(org_id: OrgId, employee_id: EmployeeId, skill_id: SkillId,
                             body: SkillAssignment, orgs: Orgs, db: UserDB) -> Response:
    await ensure_org_visible(orgs, org_id)
    await _employee(db, org_id, employee_id)
    await _skill(db, org_id, skill_id)
    statement = insert(employee_skills).values(
        employee_id=employee_id, skill_id=skill_id, proficiency=body.proficiency
    ).on_conflict_do_update(
        index_elements=[employee_skills.c.employee_id, employee_skills.c.skill_id],
        set_={'proficiency': body.proficiency},
    )
    await db.execute(statement)
    return Response(status_code=204)


@router.get('/projects/{projectId}/constraints', response_model=Constraints)
async def get_constraints(project_id: ProjectId, projects: Projects, db: UserDB) -> Constraints:
    project = await _project(project_id, projects)
    row = (await db.execute(sa.select(constraint_sets).where(
        constraint_sets.c.project_id == project_id
    ))).first()
    if row is None:
        return Constraints(project_id=project_id, budget_total_cents=project.budget_cents,
                           start_date=project.start_date, must_finish_by=project.target_end_date)
    return Constraints.model_validate(dict(row._mapping))


@router.put('/projects/{projectId}/constraints', response_model=Constraints)
async def set_constraints(project_id: ProjectId, body: ConstraintInput, projects: Projects,
                          db: UserDB) -> Constraints:
    await _project(project_id, projects)
    values = body.model_dump(by_alias=False)
    statement = insert(constraint_sets).values(project_id=project_id, **values).on_conflict_do_update(
        index_elements=[constraint_sets.c.project_id], set_=values,
    )
    await db.execute(statement)
    return Constraints(project_id=project_id, **values)


@router.get('/projects/{projectId}/dependencies', response_model=list[Dependency])
async def list_dependencies(project_id: ProjectId, projects: Projects, db: UserDB) -> list[Dependency]:
    await _project(project_id, projects)
    rows = (await db.execute(sa.select(dependencies).where(
        dependencies.c.project_id == project_id
    ).order_by(dependencies.c.predecessor_id, dependencies.c.successor_id))).all()
    return [Dependency.model_validate(dict(row._mapping)) for row in rows]


@router.post('/projects/{projectId}/dependencies', response_model=Dependency, status_code=201)
async def create_dependency(project_id: ProjectId, body: DependencyCreate, projects: Projects,
                            db: UserDB) -> Dependency:
    await _project(project_id, projects)
    # Do not rely on the trigger alone: RLS can hide a cross-project node from
    # its lookup while the foreign-key check can still observe that node.
    found = (await db.execute(sa.select(wbs_nodes_with_code.c.id).where(
        wbs_nodes_with_code.c.project_id == project_id,
        wbs_nodes_with_code.c.id.in_([body.predecessor_id, body.successor_id]),
    ))).all()
    if len(found) != 2:
        raise NotFoundError('A dependency node was not found in this project')
    # The DB trigger still detects dependency cycles atomically.
    item_id = uuid.uuid4()
    await db.execute(sa.insert(dependencies).values(
        id=item_id, project_id=project_id, **body.model_dump(by_alias=False)
    ))
    return Dependency(id=item_id, project_id=project_id, **body.model_dump(by_alias=False))


@router.delete('/projects/{projectId}/dependencies/{dependencyId}', status_code=204)
async def delete_dependency(project_id: ProjectId, dependency_id: DependencyId, projects: Projects,
                            db: UserDB) -> Response:
    await _project(project_id, projects)
    row = (await db.execute(sa.select(dependencies.c.id).where(
        dependencies.c.project_id == project_id, dependencies.c.id == dependency_id
    ))).first()
    if row is None:
        raise NotFoundError('Dependency not found')
    result = await db.execute(sa.delete(dependencies).where(
        dependencies.c.project_id == project_id, dependencies.c.id == dependency_id
    ))
    if result.rowcount == 0:
        raise ForbiddenError('You do not have permission to delete this dependency')
    return Response(status_code=204)


@router.put('/projects/{projectId}/wbs/nodes/{nodeId}/skills/{skillId}', status_code=204)
async def set_task_skill(project_id: ProjectId, node_id: NodeId, skill_id: SkillId,
                         body: SkillAssignment, projects: Projects, db: UserDB) -> Response:
    project = await _project(project_id, projects)
    node = (await db.execute(sa.select(wbs_nodes_with_code.c.id).where(
        wbs_nodes_with_code.c.project_id == project_id, wbs_nodes_with_code.c.id == node_id
    ))).first()
    if node is None:
        raise NotFoundError('WBS node not found')
    await _skill(db, project.org_id, skill_id)
    statement = insert(task_skill_requirements).values(
        wbs_node_id=node_id, skill_id=skill_id, min_proficiency=body.proficiency
    ).on_conflict_do_update(
        index_elements=[task_skill_requirements.c.wbs_node_id, task_skill_requirements.c.skill_id],
        set_={'min_proficiency': body.proficiency},
    )
    await db.execute(statement)
    return Response(status_code=204)


@router.get('/projects/{projectId}/milestones', response_model=list[Milestone])
async def list_milestones(project_id: ProjectId, projects: Projects, db: UserDB) -> list[Milestone]:
    await _project(project_id, projects)
    rows = (await db.execute(sa.select(milestones).where(
        milestones.c.project_id == project_id
    ).order_by(milestones.c.due_date, milestones.c.id))).all()
    return [Milestone.model_validate(dict(row._mapping)) for row in rows]


@router.post('/projects/{projectId}/milestones', response_model=Milestone, status_code=201)
async def create_milestone(project_id: ProjectId, body: MilestoneCreate, projects: Projects,
                           db: UserDB) -> Milestone:
    await _project(project_id, projects)
    if body.wbs_node_id is not None:
        node = (await db.execute(sa.select(wbs_nodes_with_code.c.id).where(
            wbs_nodes_with_code.c.project_id == project_id,
            wbs_nodes_with_code.c.id == body.wbs_node_id,
        ))).first()
        if node is None:
            raise NotFoundError('WBS node not found')
    item_id = uuid.uuid4()
    values = body.model_dump(by_alias=False)
    await db.execute(sa.insert(milestones).values(id=item_id, project_id=project_id, **values))
    return Milestone(id=item_id, project_id=project_id, **values)


@router.delete('/projects/{projectId}/milestones/{milestoneId}', status_code=204)
async def delete_milestone(project_id: ProjectId, milestone_id: MilestoneId, projects: Projects,
                           db: UserDB) -> Response:
    await _project(project_id, projects)
    where = [milestones.c.project_id == project_id, milestones.c.id == milestone_id]
    if (await db.execute(sa.select(milestones.c.id).where(*where))).first() is None:
        raise NotFoundError('Milestone not found')
    result = await db.execute(sa.delete(milestones).where(*where))
    if result.rowcount == 0:
        raise ForbiddenError('You do not have permission to delete this milestone')
    return Response(status_code=204)


@router.get('/orgs/{orgId}/employees/{employeeId}/skills', response_model=list[EmployeeSkill])
async def get_employee_skills(org_id: OrgId, employee_id: EmployeeId, orgs: Orgs,
                              db: UserDB) -> list[EmployeeSkill]:
    await ensure_org_visible(orgs, org_id)
    await _employee(db, org_id, employee_id)
    rows = (await db.execute(sa.select(
        skills.c.id, skills.c.name, employee_skills.c.proficiency
    ).select_from(skills.join(employee_skills, employee_skills.c.skill_id == skills.c.id)).where(
        skills.c.org_id == org_id, employee_skills.c.employee_id == employee_id
    ).order_by(skills.c.name, skills.c.id))).all()
    return [EmployeeSkill.model_validate(dict(row._mapping)) for row in rows]


@router.get('/orgs/{orgId}/employees/{employeeId}/allocations', response_model=list[EmployeeAllocation])
async def get_employee_allocations(org_id: OrgId, employee_id: EmployeeId, orgs: Orgs,
                                   db: UserDB) -> list[EmployeeAllocation]:
    await ensure_org_visible(orgs, org_id)
    await _employee(db, org_id, employee_id)
    # Aggregate without revealing the IDs or names of confidential projects.
    rows = (await db.execute(sa.select(
        allocations.c.week_start, sa.func.sum(allocations.c.hours).label("hours")
    ).where(allocations.c.employee_id == employee_id)
      .group_by(allocations.c.week_start).order_by(allocations.c.week_start))).all()
    return [EmployeeAllocation.model_validate(dict(row._mapping)) for row in rows]
