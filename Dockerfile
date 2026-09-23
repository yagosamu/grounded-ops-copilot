# syntax=docker/dockerfile:1.7

FROM --platform=linux/amd64 ghcr.io/astral-sh/uv:0.12.17@sha256:10787c682e4184e4f290de1171fd4703dc63de99221f10fe1c99002ce7fa9acc AS uv

FROM --platform=linux/amd64 python:3.13-alpine@sha256:79e7a9b9ff1cbceff819f856fb374477792a5967759d94df266de7b7b4120e6f AS runtime-base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

FROM runtime-base AS build
COPY --from=uv /uv /uvx /bin/
ENV PATH="/opt/venv/bin:${PATH}" \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

FROM runtime-base AS api
ARG VCS_REF=unknown
LABEL org.opencontainers.image.title="GroundedOps API" \
    org.opencontainers.image.source="https://github.com/yagosamu/grounded-ops-copilot" \
    org.opencontainers.image.revision="${VCS_REF}"
RUN addgroup --gid 10001 --system groundedops \
    && adduser --uid 10001 --system --disabled-password --no-create-home --ingroup groundedops --shell /sbin/nologin groundedops \
    && rm -rf /usr/local/lib/python3.13/site-packages/pip /usr/local/lib/python3.13/site-packages/pip-*.dist-info /usr/local/lib/python3.13/ensurepip
COPY --from=build --chown=10001:10001 /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "from urllib.request import urlopen; urlopen('http://127.0.0.1:8000/health/live', timeout=2)"]
CMD ["uvicorn", "grounded_ops.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
