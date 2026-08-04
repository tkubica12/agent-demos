FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY external_agent ./external_agent

RUN uv sync --frozen --no-dev

ENV HOST=0.0.0.0
ENV PORT=8000

EXPOSE 8000

CMD ["uv", "run", "--no-sync", "python", "-m", "external_agent.server"]
