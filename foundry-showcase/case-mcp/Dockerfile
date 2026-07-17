FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY case_mcp ./case_mcp

RUN uv sync --frozen --no-dev

ENV HOST=0.0.0.0
ENV PORT=8000
ENV CASE_STORE_BACKEND=table
ENV MCP_AUTH_MODE=entra_agent_identity

EXPOSE 8000

CMD ["uv", "run", "--no-sync", "python", "-m", "case_mcp.server"]
