FROM python:3.13-slim

WORKDIR /app

COPY pyproject.toml .
COPY private_incidents_mcp ./private_incidents_mcp

RUN python -m pip install --no-cache-dir .

ENV HOST=0.0.0.0
ENV PORT=8765
ENV MCP_AUTH_MODE=static_key

EXPOSE 8765

CMD ["python", "-m", "private_incidents_mcp.server"]
