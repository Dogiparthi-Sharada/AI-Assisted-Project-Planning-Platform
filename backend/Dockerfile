# syntax=docker/dockerfile:1

# ---- build: resolve and install locked dependencies into /app/.venv ----------
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Dependencies first so this layer is cached until the lockfile changes.
# (No BuildKit cache mounts: Railway requires service-specific mount ids.)
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app
RUN uv sync --frozen --no-dev

# Optional package for licensed Gurobi deployments; credentials are supplied
# only at runtime. Keep this after uv sync so the frozen sync cannot remove it.
ARG INSTALL_GUROBI=0
RUN case "$INSTALL_GUROBI" in \
      0) : ;; \
      1) uv pip install --python /app/.venv/bin/python gurobipy==13.0.0 ;; \
      *) echo "INSTALL_GUROBI must be 0 or 1" >&2; exit 1 ;; \
    esac

# ---- runtime: slim image, non-root ------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app
COPY --from=builder --chown=app:app /app /app

USER app
EXPOSE 8000

# sh -c so ${PORT} (injected by Railway) is expanded at runtime.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
