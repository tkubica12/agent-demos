FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_HTTP_RETRIES=10 \
    UV_HTTP_TIMEOUT=120 \
    UV_LINK_MODE=copy

WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:0.8.13 /uv /uvx /bin/
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY private_bot_proxy ./private_bot_proxy

USER 65532:65532
EXPOSE 8080
CMD ["/app/.venv/bin/uvicorn", "private_bot_proxy.app:app", "--host", "0.0.0.0", "--port", "8080"]
