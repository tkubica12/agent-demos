FROM ghcr.io/astral-sh/uv:0.10.2 AS uv
FROM python:3.13-slim

WORKDIR /app
COPY --from=uv /uv /bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY . .

ENV PORT=8080
CMD ["/bin/sh", "-c", "/app/.venv/bin/uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
