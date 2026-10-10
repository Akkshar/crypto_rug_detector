# syntax=docker/dockerfile:1.7

# --- dashboard bundle --------------------------------------------------------
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# --- python runtime ------------------------------------------------------------
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv

WORKDIR /app

# Dependency layer – only re-runs when the lock or project metadata changes.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project --extra live --extra api

COPY config ./config
COPY src ./src
COPY main.py ./
COPY tests/fixtures ./tests/fixtures
COPY --from=web /web/dist ./web/dist
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra live --extra api

EXPOSE 8000

RUN useradd --create-home --uid 1000 rugscan && chown -R rugscan:rugscan /app
USER rugscan

ENV PATH="/app/.venv/bin:$PATH"
ENTRYPOINT ["python", "main.py"]
CMD ["--help"]
