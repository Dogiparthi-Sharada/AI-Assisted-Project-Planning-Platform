# Testing the planning platform

Run these commands from the repository root unless a step says otherwise.

## Offline planning checks — Python only

These checks require no package installation or internet connection:

```bash
cd backend
python -m unittest discover -s tests/offline -v
```

They check scheduling helpers and a small independently enumerated example.
They also test selected Gurobi error handling with stubs. They do not run the
Gurobi or OR-Tools solver, the API, or the database.

With Node.js installed, the hosting configuration checks also run offline:

```bash
node --test huggingface/tests/build-static.test.mjs
```

## Install dependencies

The root `requirements.txt` installs the pinned Python backend packages plus
`gurobipy`. The development list adds pytest and other checking tools:

```bash
python -m pip install -r requirements.txt
python -m pip install -r backend/requirements-dev.txt
corepack enable
pnpm install --frozen-lockfile
```

Installation needs access to the relevant package registries. The project also
supports `uv sync --frozen` in `backend`; see `backend/README.md`. Installing
the Gurobi package does not configure a license suitable for your environment.

## Run solver and application tests

```bash
cd backend
python -m pytest -q
GUROBI_TEST_REQUIRED=1 python -m pytest -q tests/test_gurobi_solver.py
python -m ruff check .
python -m ruff format --check .
python -m mypy app
cd ..
pnpm exec tsc --noEmit -p .
pnpm build
```

The required Gurobi test fails if Gurobi cannot be imported or a license cannot
be started. A license may require a network connection even after installation.
The frontend and backend suites do not replace the Supabase database/RLS checks
or a complete browser test. See `supabase/README.md` and the CI workflow for
database checks. These tests must pass before deploying the draft.
