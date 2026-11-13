ARG HERMES_BASE_IMAGE
FROM alpine/git:2.49.1 AS minimax-skills

ARG MINIMAX_SKILLS_COMMIT=60aaae52bb2af8162732751a4332f62a5fef518b
RUN git clone https://github.com/MiniMax-AI/skills.git /source \
    && cd /source \
    && git checkout "${MINIMAX_SKILLS_COMMIT}" \
    && test "$(git rev-parse HEAD)" = "${MINIMAX_SKILLS_COMMIT}"

FROM mcr.microsoft.com/dotnet/sdk:8.0 AS minimax-docx

WORKDIR /src
COPY --from=minimax-skills /source/skills/minimax-docx/scripts/dotnet/ .
RUN dotnet restore MiniMaxAIDocx.Cli/MiniMaxAIDocx.Cli.csproj -r linux-x64 \
    --source https://packagefeedproxy.microsoft.io/nuget/v3/index.json \
    && dotnet publish MiniMaxAIDocx.Cli/MiniMaxAIDocx.Cli.csproj --no-restore \
    -c Release -r linux-x64 --self-contained true \
    -p:PublishSingleFile=true -p:PublishTrimmed=false -o /out

FROM mcr.microsoft.com/dotnet/sdk:10.0 AS office-helper

WORKDIR /src
COPY runtimes/hermes/office_helper/OfficeCollaboration.csproj .
RUN dotnet restore -r linux-x64 --source https://packagefeedproxy.microsoft.io/nuget/v3/index.json
COPY runtimes/hermes/office_helper/Program.cs .
RUN dotnet publish --no-restore -c Release -r linux-x64 --self-contained true \
    -p:PublishSingleFile=true -p:PublishTrimmed=false -o /out

FROM mcr.microsoft.com/devcontainers/javascript-node:22-bookworm AS office-node

WORKDIR /app
COPY runtimes/hermes/package.json runtimes/hermes/package-lock.json ./
RUN npm ci --ignore-scripts --registry=https://packagefeedproxy.microsoft.io/npm/

FROM ${HERMES_BASE_IMAGE}
USER root
COPY --from=ghcr.io/astral-sh/uv:0.12.9 /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates ripgrep pandoc libreoffice-writer libreoffice-calc libreoffice-impress \
        poppler-utils qpdf \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=office-node /app/node_modules ./node_modules

COPY runtimes/hermes/pyproject.toml runtimes/hermes/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --python /usr/bin/python3.13 --no-managed-python

COPY runtimes/hermes/hermes-site.txt /opt/autopilots/hermes-site.txt
RUN uv pip install --python /opt/hermes/.venv/bin/python --target /opt/autopilots/hermes-site \
        --no-deps --require-hashes --index-url https://packagefeedproxy.microsoft.io/pypi/simple/ \
        -r /opt/autopilots/hermes-site.txt \
    && /opt/hermes/.venv/bin/python -c "import sys; sys.path.append('/opt/autopilots/hermes-site'); import azure.servicebus, azure.monitor.opentelemetry.exporter, yaml"

COPY --from=minimax-skills /source/skills/minimax-docx ./runtime_skills/minimax-docx
COPY --from=minimax-skills /source/skills/minimax-xlsx ./runtime_skills/minimax-xlsx
COPY --from=minimax-skills /source/skills/pptx-generator ./runtime_skills/pptx-generator
COPY --from=minimax-skills /source/skills/minimax-pdf ./runtime_skills/minimax-pdf
COPY --from=office-helper /out/office-collaboration /usr/local/bin/office-collaboration
COPY --from=minimax-docx /out/MiniMaxAIDocx.Cli /usr/local/bin/minimax-docx

ENV HERMES_HOME=/data/hermes
ENV HERMES_BUNDLED_SKILLS=/app/runtime_skills
ENV AUTOPILOTS_PLUGIN_PATH=/app:/opt/autopilots/hermes-site
ENV PYTHONPATH=/app
ENV PATH="/app/.venv/bin:${PATH}"
ENV NODE_PATH=/app/node_modules
ENV PYTHONUNBUFFERED=1
ENV PORT=8642

EXPOSE 8642

ENTRYPOINT ["/bin/sleep", "infinity"]
