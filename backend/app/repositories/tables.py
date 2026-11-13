"""SQLAlchemy Core mappings of the existing Supabase tables.

The schema is owned by `supabase/migrations`; these definitions only describe
the columns the API reads/writes. Never call `metadata.create_all()`.
"""

from __future__ import annotations

from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, ENUM, JSONB, UUID

from app.domain.common import (
    ConstraintMode,
    DependencyType,
    NotificationLevel,
    OrgRole,
    ProjectMethodology,
    ProjectPriority,
    ProjectStatus,
    ProjectStep,
    ProjectType,
    ProjectVisibility,
    RecommendationType,
    SolverStatus,
)

metadata = sa.MetaData(schema="public")


def _pg_enum(enum_cls: type[StrEnum], name: str) -> ENUM:
    return ENUM(
        enum_cls,
        name=name,
        schema="public",
        create_type=False,
        values_callable=lambda e: [m.value for m in e],
        validate_strings=True,
    )


org_role = _pg_enum(OrgRole, "org_role")
project_type = _pg_enum(ProjectType, "project_type")
project_priority = _pg_enum(ProjectPriority, "project_priority")
project_methodology = _pg_enum(ProjectMethodology, "project_methodology")
project_visibility = _pg_enum(ProjectVisibility, "project_visibility")
project_status = _pg_enum(ProjectStatus, "project_status")
project_step = _pg_enum(ProjectStep, "project_step")
notification_level = _pg_enum(NotificationLevel, "notification_level")

_uuid = UUID(as_uuid=True)
_ts = sa.DateTime(timezone=True)

profiles = sa.Table(
    "profiles",
    metadata,
    sa.Column("id", _uuid, primary_key=True),
    sa.Column("email", sa.Text, nullable=False),  # citext
    sa.Column("full_name", sa.Text),
    sa.Column("avatar_url", sa.Text),
    sa.Column("job_title", sa.Text),
    sa.Column("created_at", _ts, nullable=False),
    sa.Column("updated_at", _ts, nullable=False),
)

organizations = sa.Table(
    "organizations",
    metadata,
    sa.Column("id", _uuid, primary_key=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("slug", sa.Text, nullable=False),  # citext
    sa.Column("created_by", _uuid),
    sa.Column("created_at", _ts, nullable=False),
    sa.Column("updated_at", _ts, nullable=False),
)

organization_members = sa.Table(
    "organization_members",
    metadata,
    sa.Column("org_id", _uuid, primary_key=True),
    sa.Column("user_id", _uuid, primary_key=True),
    sa.Column("role", org_role, nullable=False),
    sa.Column("created_at", _ts, nullable=False),
)

projects = sa.Table(
    "projects",
    metadata,
    sa.Column("id", _uuid, primary_key=True),
    sa.Column("org_id", _uuid, nullable=False),
    sa.Column("code", sa.Text, nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("description", sa.Text),
    sa.Column("type", project_type, nullable=False),
    sa.Column("department", sa.Text),
    sa.Column("sponsor", sa.Text),
    sa.Column("pm_user_id", _uuid),
    sa.Column("priority", project_priority, nullable=False),
    sa.Column("methodology", project_methodology, nullable=False),
    sa.Column("visibility", project_visibility, nullable=False),
    sa.Column("start_date", sa.Date),
    sa.Column("target_end_date", sa.Date),
    sa.Column("budget_cents", sa.BigInteger),
    sa.Column("currency", sa.CHAR(3), nullable=False),
    sa.Column("status", project_status, nullable=False),
    sa.Column("current_step", project_step, nullable=False),
    sa.Column("progress_pct", sa.SmallInteger, nullable=False),
    sa.Column("created_by", _uuid),
    sa.Column("created_at", _ts, nullable=False),
    sa.Column("updated_at", _ts, nullable=False),
    sa.Column("archived_at", _ts),
)

notifications = sa.Table(
    "notifications",
    metadata,
    sa.Column("id", _uuid, primary_key=True),
    sa.Column("user_id", _uuid, nullable=False),
    sa.Column("org_id", _uuid, nullable=False),
    sa.Column("project_id", _uuid),
    sa.Column("level", notification_level, nullable=False),
    sa.Column("message", sa.Text, nullable=False),
    sa.Column("read_at", _ts),
    sa.Column("created_at", _ts, nullable=False),
)

# --------------------------------------------------------------------------- planning / optimizer
solver_status = _pg_enum(SolverStatus, "solver_status")
dependency_type = _pg_enum(DependencyType, "dependency_type")
constraint_mode = _pg_enum(ConstraintMode, "constraint_mode")
recommendation_type = _pg_enum(RecommendationType, "recommendation_type")

skills = sa.Table(
    "skills",
    metadata,
    sa.Column("id", _uuid, primary_key=True),
    sa.Column("org_id", _uuid, nullable=False),
    sa.Column("name", sa.Text, nullable=False),  # citext
)

employees = sa.Table(
    "employees",
    metadata,
    sa.Column("id", _uuid, primary_key=True),
    sa.Column("org_id", _uuid, nullable=False),
    sa.Column("full_name", sa.Text, nullable=False),
    sa.Column("role_title", sa.Text),
    sa.Column("weekly_capacity_hours", sa.Numeric(5, 1), nullable=False),
    sa.Column("hourly_rate_cents", sa.BigInteger),
    sa.Column("is_contractor", sa.Boolean, nullable=False),
    sa.Column("active", sa.Boolean, nullable=False),
)

employee_skills = sa.Table(
    "employee_skills",
    metadata,
    sa.Column("employee_id", _uuid, primary_key=True),
    sa.Column("skill_id", _uuid, primary_key=True),
    sa.Column("proficiency", sa.SmallInteger, nullable=False),
)

# A view (security_invoker): WBS codes derived from tree position.
wbs_nodes_with_code = sa.Table(
    "wbs_nodes_with_code",
    metadata,
    sa.Column("id", _uuid, primary_key=True),
    sa.Column("project_id", _uuid, nullable=False),
    sa.Column("parent_id", _uuid),
    sa.Column("level", sa.SmallInteger, nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("duration_days", sa.Integer),
    sa.Column("effort_hours", sa.Numeric(8, 1)),
    sa.Column("owner_employee_id", _uuid),
    sa.Column("code", sa.Text, nullable=False),
    sa.Column("path", ARRAY(sa.Integer), nullable=False),
)

task_skill_requirements = sa.Table(
    "task_skill_requirements",
    metadata,
    sa.Column("wbs_node_id", _uuid, primary_key=True),
    sa.Column("skill_id", _uuid, primary_key=True),
    sa.Column("min_proficiency", sa.SmallInteger, nullable=False),
)

dependencies = sa.Table(
    "dependencies",
    metadata,
    sa.Column("id", _uuid, primary_key=True),
    sa.Column("project_id", _uuid, nullable=False),
    sa.Column("predecessor_id", _uuid, nullable=False),
    sa.Column("successor_id", _uuid, nullable=False),
    sa.Column("type", dependency_type, nullable=False),
    sa.Column("lag_days", sa.Integer, nullable=False),
)

constraint_sets = sa.Table(
    "constraint_sets",
    metadata,
    sa.Column("project_id", _uuid, primary_key=True),
    sa.Column("budget_total_cents", sa.BigInteger),
    sa.Column("contingency_pct", sa.Numeric(5, 2), nullable=False),
    sa.Column("infra_cap_cents", sa.BigInteger),
    sa.Column("labor_cap_cents", sa.BigInteger),
    sa.Column("budget_mode", constraint_mode, nullable=False),
    sa.Column("budget_penalty", sa.REAL, nullable=False),
    sa.Column("start_date", sa.Date),
    sa.Column("must_finish_by", sa.Date),
    sa.Column("deadline_mode", constraint_mode, nullable=False),
    sa.Column("deadline_penalty_per_day", sa.REAL, nullable=False),
    sa.Column("working_days_per_week", sa.SmallInteger, nullable=False),
    sa.Column("capacity_mode", constraint_mode, nullable=False),
    sa.Column("capacity_penalty", sa.REAL, nullable=False),
    sa.Column("skill_mode", constraint_mode, nullable=False),
    sa.Column("allow_contractors", sa.Boolean, nullable=False),
    sa.Column("contractor_rate_cents", sa.BigInteger),
)

milestones = sa.Table(
    "milestones",
    metadata,
    sa.Column("id", _uuid, primary_key=True),
    sa.Column("project_id", _uuid, nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("due_date", sa.Date, nullable=False),
    sa.Column("wbs_node_id", _uuid),
    sa.Column("mode", constraint_mode, nullable=False),
)

allocations = sa.Table(
    "allocations",
    metadata,
    sa.Column("employee_id", _uuid, primary_key=True),
    sa.Column("project_id", _uuid, primary_key=True),
    sa.Column("week_start", sa.Date, primary_key=True),
    sa.Column("hours", sa.Numeric(5, 1), nullable=False),
)

optimization_runs = sa.Table(
    "optimization_runs",
    metadata,
    sa.Column("id", _uuid, primary_key=True),
    sa.Column("project_id", _uuid, nullable=False),
    sa.Column("status", solver_status, nullable=False),
    sa.Column("solver", sa.Text),
    sa.Column("solver_version", sa.Text),
    sa.Column("runtime_ms", sa.Integer),
    sa.Column("mip_gap", sa.REAL),
    sa.Column("objective_value", sa.Double),
    sa.Column("expected_cost_cents", sa.BigInteger),
    sa.Column("completion_date", sa.Date),
    sa.Column("avg_utilization", sa.REAL),
    sa.Column("critical_path", JSONB),
    sa.Column("input_snapshot", JSONB, nullable=False),
    sa.Column("infeasibility", JSONB),
    sa.Column("result", JSONB),
    sa.Column("error", sa.Text),
    sa.Column("requested_by", _uuid),
    sa.Column("started_at", _ts),
    sa.Column("finished_at", _ts),
    sa.Column("created_at", _ts, nullable=False),
)

planned_assignments = sa.Table(
    "planned_assignments",
    metadata,
    # gen_random_uuid() default in the database
    sa.Column("id", _uuid, primary_key=True, server_default=sa.FetchedValue()),
    sa.Column("run_id", _uuid, nullable=False),
    sa.Column("wbs_node_id", _uuid, nullable=False),
    sa.Column("employee_id", _uuid),
    sa.Column("contractor_label", sa.Text),
    sa.Column("start_week", sa.Integer, nullable=False),
    sa.Column("end_week", sa.Integer, nullable=False),
    sa.Column("hours_per_week", sa.Numeric(5, 1), nullable=False),
    sa.Column("baseline_start_week", sa.Integer),
    sa.Column("baseline_end_week", sa.Integer),
    sa.Column("baseline_employee_id", _uuid),
)

recommendations = sa.Table(
    "recommendations",
    metadata,
    # gen_random_uuid() default in the database
    sa.Column("id", _uuid, primary_key=True, server_default=sa.FetchedValue()),
    sa.Column("run_id", _uuid, nullable=False),
    sa.Column("type", recommendation_type, nullable=False),
    sa.Column("wbs_node_id", _uuid),
    sa.Column("before_value", sa.Text),
    sa.Column("after_value", sa.Text),
    sa.Column("reason", sa.Text, nullable=False),
    sa.Column("sort_order", sa.Integer, nullable=False),
)
