FROM mcr.microsoft.com/devcontainers/javascript-node:24-bookworm

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip python3-venv git ca-certificates curl ripgrep \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g openclaw@latest

COPY requirements.txt .
RUN python3 -m pip install --break-system-packages --no-cache-dir -r requirements.txt

COPY openclaw_gateway ./openclaw_gateway

ENV PORT=18789
ENV PERSISTENCE_BACKEND=file
ENV OPENCLAW_DATA_DIR=/data
ENV OPENCLAW_WORKSPACE_DIR=/data/workspace
ENV OPENCLAW_HOME_DIR=/data/home
CMD ["python3", "-m", "openclaw_gateway.start_gateway"]
