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
RUN /app/.venv/bin/python -B -c "from bridge.app import app; assert app is not None"
ENV PYTHONPATH=/app
EXPOSE 8001
ENTRYPOINT ["/app/.venv/bin/python", "-m", "uvicorn", "bridge.app:app", "--host", "0.0.0.0", "--port", "8001", "--no-access-log"]
