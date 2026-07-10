FROM mcr.microsoft.com/devcontainers/javascript-node:22-bookworm

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip python3-venv git ca-certificates curl ripgrep \
    && rm -rf /var/lib/apt/lists/*

COPY runtimes/hermes/requirements.txt .
RUN python3 -m pip install --break-system-packages --no-cache-dir -r requirements.txt

COPY runtimes/hermes/start_hermes.py .
COPY runtimes/hermes/foundry_token_proxy.py .
COPY autopilots_identity ./autopilots_identity

ENV API_SERVER_ENABLED=true
ENV API_SERVER_HOST=0.0.0.0
ENV API_SERVER_PORT=8642
ENV HERMES_HOME=/data/hermes
ENV PYTHONUNBUFFERED=1
ENV PORT=8642

EXPOSE 8642

CMD ["python3", "start_hermes.py"]
