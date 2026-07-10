FROM python:3.13-slim

WORKDIR /app

COPY public-shipments-mcp/pyproject.toml .
COPY public-shipments-mcp/public_shipments_mcp ./public_shipments_mcp

RUN python -m pip install --no-cache-dir .

ENV HOST=0.0.0.0
ENV PORT=8765
ENV MCP_AUTH_MODE=entra

EXPOSE 8765

CMD ["python", "-m", "public_shipments_mcp.server"]
