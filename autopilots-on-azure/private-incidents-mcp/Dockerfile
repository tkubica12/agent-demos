FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.12.9 /uv /usr/local/bin/uv

WORKDIR /app
COPY private-incidents-mcp/pyproject.toml private-incidents-mcp/uv.lock ./
COPY private-incidents-mcp/private_incidents_mcp ./private_incidents_mcp

RUN uv sync --frozen --no-dev --no-install-project

ENV PATH="/app/.venv/bin:${PATH}"
ENV HOST=0.0.0.0
ENV PORT=8765
ENV MCP_AUTH_MODE=entra_agent_identity

EXPOSE 8765

CMD ["uv", "run", "--no-sync", "python", "-m", "private_incidents_mcp.server"]
