# Project Planning API (backend)

FastAPI service for the AI-Assisted Project Planning Platform. It is a modular
monolith with an optimization worker and planning API.

- **Database:** Supabase Postgres. The schema lives in `../supabase/migrations`
  and belongs to the DB team. This service has no migrations and never creates tables.
- **Auth:** Supabase Auth. Clients send `Authorization: Bearer <access token>`.
  Tokens are verified against the project's JWKS (ES256/RS256). HS256 is accepted
  only if `SUPABASE_JWT_SECRET` is set.
- **Authorization:** Postgres RLS. Every user request runs in its own transaction
  with `request.jwt.claims` / `request.jwt.claim.sub` set and `set local role authenticated`,
  so `auth.uid()` and every policy work the same as through the Supabase API
  (`app/core/db.py:get_user_db`). `get_service_db` skips RLS and is only for trusted
  system jobs. User-facing endpoints must never use it.

## Layout

```
app/
  main.py            app factory: middleware, routers, exception handlers
  core/              config, db (engine + RLS sessions), security (JWT), errors (RFC 7807),
                     logging (JSON), middleware (request id, access log, security headers),
                     pagination (keyset cursors)
  api/deps.py        repository providers (tests override these)
  api/v1/            routers: me, orgs, projects, dashboard, notifications, optimization, health
  optimization/      planning model, CP-SAT / optional Gurobi solvers, explanations
  jobs/              in-process background worker for optimization runs
  domain/            pydantic schemas (camelCase JSON) + Postgres enum mirrors
  repositories/      SQLAlchemy Core queries against the existing tables (tables.py)
tests/               unit + API tests (no database needed)
```

## Run locally

Requires [uv](https://docs.astral.sh/uv/). The Python version is pinned in `.python-version`.

```bash
cd backend
uv sync
cp .env.example .env        # then fill in DATABASE_URL, SUPABASE_URL, CORS_ORIGINS
uv run uvicorn app.main:app --reload --port 8000
```

- Liveness: <http://localhost:8000/health>
- Readiness (checks the DB): <http://localhost:8000/health/ready>
- OpenAPI docs: <http://localhost:8000/api/docs>. They're off in production unless `ENABLE_DOCS=true`.

Without `DATABASE_URL`/`SUPABASE_URL`, the app still starts in development.
`/health` works, and endpoints that need the DB or auth return `503`.

## Test and lint

```bash
uv run pytest -q                       # tests
uv run coverage run -m pytest -q && uv run coverage report
uv run ruff check .                    # lint
uv run ruff format .                   # format (use --check in CI)
uv run mypy app                        # type check (strict)
```

The tests use in-memory fakes for the repositories, a generated EC key with a
mocked JWKS for JWT verification, and a recording connection that checks the
RLS session setup and the generated SQL. They don't cover RLS itself. Test that
against a real Supabase instance, e.g. `supabase start` plus a seeded user.

## API

All endpoints are under `/api/v1` except health. JSON uses camelCase. Errors are
`application/problem+json` (RFC 7807) and include a `requestId`.

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | `{status, version}`. No DB access. |
| GET | `/health/ready` | Runs `select 1`. Returns 503 if the DB is unreachable. |
| GET | `/api/v1/me` | Profile plus `organizations[{id,name,slug,role}]` |
| PATCH | `/api/v1/me` | `fullName`, `jobTitle` |
| GET | `/api/v1/orgs/{orgId}/members` | Roster with profile names (PM dropdown) |
| GET | `/api/v1/orgs/{orgId}/projects` | `status` (repeatable), `q`, `limit` (1–100, default 25), `cursor`. Returns `{items, nextCursor}`. |
| POST | `/api/v1/orgs/{orgId}/projects` | Returns 201 plus a `Location` header |
| GET/PATCH/DELETE | `/api/v1/projects/{projectId}` | Partial PATCH. DELETE is a hard delete and returns 204. |
| GET | `/api/v1/orgs/{orgId}/dashboard` | `{totalProjects, onTrack, atRiskOrDelayed, totalBudgetCents, createdThisMonth}` |
| GET | `/api/v1/notifications` | `unread=true/false`, `limit`, `cursor` |
| POST | `/api/v1/notifications/{id}/read` | Idempotent. Returns the notification. |
| POST | `/api/v1/projects/{projectId}/optimization/runs` | 202 + `OptimizationRun` (`queued`), solved in the background. 403 not a writer, 404 project not visible, 409 a run is already queued/running, 422 nothing to optimize (`missing: ["workPackages", "team"]`). |
| GET | `/api/v1/projects/{projectId}/optimization/runs/latest` | Latest run, or 404 when there are none |
| GET | `/api/v1/projects/{projectId}/optimization/runs/{runId}` | One run |

Error mapping: RLS violation / `42501` → 403, trigger `P0001` → 422 (with the trigger's
message), `23505` → 409, `23514`/`23503`/`23502`/`22xxx` → 422, missing row → 404,
DB unreachable / pool exhausted / statement timeout → 503.

## Optimizer

`app/optimization/` contains the planning model and solvers (no DB or FastAPI imports):
`model.py` (input/output + the frozen `input_snapshot` format), `baseline.py`
(forward-pass "before" plan), `solver.py` (model, status mapping, infeasibility
explanation via assumption literals; the module docstring documents the formulation
and its simplifications) and `explain.py` (the UI `result` payload and DB rows).

`POST .../optimization/runs` loads the plan under the caller's RLS session, freezes it
into `input_snapshot` and commits. A background task then solves it in-process
(`app/jobs/optimizer.py`, `asyncio.to_thread`) and writes the outcome, plus
`planned_assignments` and `recommendations`, on a service connection. On startup,
and before answering 409, runs stuck in queued/running for longer than
`OPTIMIZER_STALE_AFTER_S` are marked failed, because a restart orphans them. Tuning:
`OPTIMIZER_TIME_LIMIT_S`, `OPTIMIZER_WORKERS` (see `.env.example`).

`OPTIMIZER_SOLVER` defaults to `cpsat`. For Gurobi, install the package, configure
an appropriate Gurobi license for the target environment and use, and set
`OPTIMIZER_SOLVER=gurobi`. The backend does **not** silently fall back to CP-SAT:
a missing package or unusable license produces a failed optimization run.

For local testing, after `uv sync` install the same pinned package used in CI,
then use `--no-sync` so a frozen sync does not remove this optional package:

```bash
uv pip install --python .venv/bin/python gurobipy==13.0.0
GUROBI_TEST_REQUIRED=1 uv run --no-sync pytest -q tests/test_gurobi_solver.py
```

The limited license bundled with the Python package is suitable only for small
non-production tests and has model-size and usage restrictions. An integration
test that skips because of a missing package or license does not prove Gurobi ran.
Verify any larger model with a license appropriate to its intended environment.

To include the package in the backend Docker image without changing the default
CP-SAT image, set the non-secret build argument `INSTALL_GUROBI=1`:

```bash
docker build --build-arg INSTALL_GUROBI=1 -t project-api-gurobi ./backend
```

In Railway, set `INSTALL_GUROBI=1` as an API service build variable and rebuild
the image. Configure `OPTIMIZER_SOLVER=gurobi` at runtime and provide a valid
license securely at runtime—for example, via a securely mounted license file
and `GRB_LICENSE_FILE` when supported by the hosting environment. Never pass
license credentials through a build argument or commit them to the repository.
Installing the package does not grant a production license. Test a real solve
with your license and production-sized inputs before relying on the deployment.

## Deploy (Railway)

The service deploys from `backend/Dockerfile`. It uses a uv builder, then a slim
runtime image that runs as a non-root user.

1. Create a Railway service from this repo. Set **Root Directory** to `/backend`.
   Set the **config file path** to `/backend/railway.json`, because the config
   file path doesn't follow the root directory.
2. Set the variables from `.env.example`: `ENVIRONMENT=production`, `DATABASE_URL`
   (Supavisor **session pooler** URL, IPv4), `DATABASE_SSL=require`, `SUPABASE_URL`,
   `CORS_ORIGINS`, and optionally `SUPABASE_JWT_SECRET` and `LOG_LEVEL`.
   Railway provides `PORT` and `RAILWAY_GIT_COMMIT_SHA`.
3. Railway health-checks `/health`. Only changes under `backend/**` trigger a redeploy.
