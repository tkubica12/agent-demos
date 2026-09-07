FROM python:3.14-slim
COPY --from=ghcr.io/astral-sh/uv:0.12.9 /uv /usr/local/bin/uv

WORKDIR /app

COPY public-shipments-mcp/pyproject.toml public-shipments-mcp/uv.lock ./
COPY public-shipments-mcp/public_shipments_mcp ./public_shipments_mcp

RUN uv sync --frozen --no-dev --no-install-project

ENV PATH="/app/.venv/bin:${PATH}"
ENV HOST=0.0.0.0
ENV PORT=8765
ENV MCP_AUTH_MODE=entra

EXPOSE 8765

CMD ["uv", "run", "--no-sync", "python", "-m", "public_shipments_mcp.server"]
