# syntax=docker/dockerfile:1.7
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
    uv sync --frozen --no-dev --no-install-project --extra live

COPY config ./config
COPY src ./src
COPY main.py ./
COPY tests/fixtures ./tests/fixtures
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra live

RUN useradd --create-home --uid 1000 rugscan && chown -R rugscan:rugscan /app
USER rugscan

ENV PATH="/app/.venv/bin:$PATH"
ENTRYPOINT ["python", "main.py"]
CMD ["--help"]
