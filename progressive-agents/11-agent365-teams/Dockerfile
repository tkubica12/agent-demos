FROM ghcr.io/astral-sh/uv:0.8.22 AS uv
FROM python:3.13-slim

WORKDIR /app

COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .

ENV PATH="/app/.venv/bin:$PATH"
ENV PORT=8080
CMD ["python", "main.py"]
