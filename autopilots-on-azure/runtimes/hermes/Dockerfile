FROM alpine/git:2.49.1 AS minimax-skills

ARG MINIMAX_SKILLS_COMMIT=60aaae52bb2af8162732751a4332f62a5fef518b
RUN git clone https://github.com/MiniMax-AI/skills.git /source \
    && cd /source \
    && git checkout "${MINIMAX_SKILLS_COMMIT}" \
    && test "$(git rev-parse HEAD)" = "${MINIMAX_SKILLS_COMMIT}"

FROM mcr.microsoft.com/dotnet/sdk:8.0 AS minimax-docx

WORKDIR /src
COPY --from=minimax-skills /source/skills/minimax-docx/scripts/dotnet/ .
RUN dotnet publish MiniMaxAIDocx.Cli/MiniMaxAIDocx.Cli.csproj \
    -c Release -r linux-x64 --self-contained true \
    -p:PublishSingleFile=true -p:PublishTrimmed=false -o /out

FROM mcr.microsoft.com/dotnet/sdk:10.0 AS office-helper

WORKDIR /src
COPY runtimes/hermes/office_helper/OfficeCollaboration.csproj .
RUN dotnet restore
COPY runtimes/hermes/office_helper/Program.cs .
RUN dotnet publish -c Release -r linux-x64 --self-contained true \
    -p:PublishSingleFile=true -p:PublishTrimmed=false -o /out

FROM mcr.microsoft.com/devcontainers/javascript-node:22-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.12.9 /uv /usr/local/bin/uv

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv git ca-certificates curl ripgrep \
        pandoc libreoffice-writer libreoffice-calc libreoffice-impress \
        poppler-utils qpdf \
    && rm -rf /var/lib/apt/lists/*
COPY runtimes/hermes/package.json runtimes/hermes/package-lock.json ./
RUN npm ci --ignore-scripts

COPY runtimes/hermes/pyproject.toml runtimes/hermes/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --python python3 --no-managed-python

COPY runtimes/hermes/start_hermes.py .
COPY runtimes/hermes/blueprint.py .
COPY runtimes/hermes/learning.py .
COPY runtimes/hermes/collective_learning.py .
COPY runtimes/hermes/azure_cron_provider.py .
COPY runtimes/hermes/cron_runtime.py .
COPY runtimes/hermes/plugins ./runtime_plugins
COPY runtimes/hermes/runtime_skills ./runtime_skills
COPY --from=minimax-skills /source/skills/minimax-docx ./runtime_skills/minimax-docx
COPY --from=minimax-skills /source/skills/minimax-xlsx ./runtime_skills/minimax-xlsx
COPY --from=minimax-skills /source/skills/pptx-generator ./runtime_skills/pptx-generator
COPY --from=minimax-skills /source/skills/minimax-pdf ./runtime_skills/minimax-pdf
COPY autopilots_identity ./autopilots_identity
COPY bridge/__init__.py bridge/telemetry.py bridge/telemetry_http.py bridge/hermes_telemetry.py ./bridge/
COPY --from=office-helper /out/office-collaboration /usr/local/bin/office-collaboration
COPY --from=minimax-docx /out/MiniMaxAIDocx.Cli /usr/local/bin/minimax-docx

ENV API_SERVER_ENABLED=true
ENV API_SERVER_HOST=0.0.0.0
ENV API_SERVER_PORT=8642
ENV HERMES_HOME=/data/hermes
ENV HERMES_BUNDLED_SKILLS=/app/runtime_skills
ENV PYTHONPATH=/app
ENV PATH="/app/.venv/bin:${PATH}"
ENV NODE_PATH=/app/node_modules
ENV UV_PYTHON_DOWNLOADS=never
ENV PYTHONUNBUFFERED=1
ENV PORT=8642

EXPOSE 8642

CMD ["uv", "run", "--no-sync", "python", "start_hermes.py"]
