FROM node:24-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json frontend/.npmrc ./
RUN npm ci --no-audit --no-fund --registry=https://packagefeedproxy.microsoft.io/npm/
RUN readlink node_modules/.bin/tsc && stat -Lc '%a %n' node_modules/.bin/tsc && test -x node_modules/.bin/tsc
COPY frontend/index.html frontend/tsconfig.json frontend/vite.config.mjs frontend/THIRD-PARTY-NOTICES.txt ./
COPY frontend/src/ ./src/
COPY frontend/scripts/prepare-assets.mjs ./scripts/prepare-assets.mjs
RUN readlink node_modules/.bin/tsc && stat -Lc '%a %n' node_modules/.bin/tsc && test -x node_modules/.bin/tsc
RUN npm run build

FROM python:3.15-slim-bookworm AS python-dependencies
COPY --from=ghcr.io/astral-sh/uv:0.12.9 /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy \
    UV_DEFAULT_INDEX=https://packagefeedproxy.microsoft.io/pypi/simple/
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --no-managed-python

FROM python:3.15-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:${PATH}" ENV=production \
    TSIM_DATABASE_URL=sqlite:////data/simulator.db TSIM_STORAGE_PATH=/data/files
RUN groupadd --gid 10001 simulator \
    && useradd --uid 10001 --gid simulator --no-create-home simulator \
    && mkdir -p /data/files && chown -R simulator:simulator /data
WORKDIR /app
COPY --from=python-dependencies /app/.venv ./.venv
COPY backend/ ./backend/
COPY --from=frontend /build/dist ./frontend/dist
ARG SIMULATOR_CONTAINER_USER=10001:10001
USER ${SIMULATOR_CONTAINER_USER}
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"
ENTRYPOINT ["python", "-m", "backend.container_start", "simulator"]
