ARG PYTHON_BASE_IMAGE
FROM ${PYTHON_BASE_IMAGE}
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:0.12.9 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY bridge/*.py ./bridge/
COPY bridge/runtime/*.py ./bridge/runtime/
COPY autopilots_identity/*.py ./autopilots_identity/
COPY chat-simulator/backend/container_start.py ./container_start.py
RUN /app/.venv/bin/python -B -c "from bridge.app import app; import container_start; assert app is not None and callable(container_start.main)"
ENV PYTHONPATH=/app
EXPOSE 8001
ENTRYPOINT ["/app/.venv/bin/python", "/app/container_start.py", "gateway"]
