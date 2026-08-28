FROM ghcr.io/astral-sh/uv:0.6.9 AS uv

FROM python:3.13-slim

COPY --from=uv /uv /uvx /bin/

RUN apt-get update \
    && apt-get install -y --no-install-recommends openssl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /runner
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
ENV PATH="/runner/.venv/bin:$PATH"
RUN certbot --version

COPY runner.py .
ENTRYPOINT ["/runner/.venv/bin/python", "/runner/runner.py"]
