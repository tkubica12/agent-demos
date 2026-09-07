FROM python:3.14-slim
COPY --from=ghcr.io/astral-sh/uv:0.12.9 /uv /usr/local/bin/uv

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY bridge ./bridge
COPY scripts ./scripts

ENV PYTHONUNBUFFERED=1
ENV PATH="/app/.venv/bin:${PATH}"
ENV PORT=8000

ENTRYPOINT ["uv", "run", "--no-sync", "python", "-m", "uvicorn", "bridge.app:app", "--host", "0.0.0.0", "--port", "8000"]
