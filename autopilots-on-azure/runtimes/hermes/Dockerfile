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

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv git ca-certificates curl ripgrep \
        pandoc libreoffice-writer libreoffice-calc libreoffice-impress \
        poppler-utils qpdf \
    && rm -rf /var/lib/apt/lists/*
RUN npm install --global docx pptxgenjs

COPY runtimes/hermes/requirements.txt .
RUN python3 -m pip install --break-system-packages --no-cache-dir -r requirements.txt

COPY runtimes/hermes/start_hermes.py .
COPY runtimes/hermes/blueprint.py .
COPY runtimes/hermes/learning.py .
COPY runtimes/hermes/collective_learning.py .
COPY runtimes/hermes/foundry_token_proxy.py .
COPY runtimes/hermes/azure_cron_provider.py .
COPY runtimes/hermes/cron_runtime.py .
COPY runtimes/hermes/plugins ./runtime_plugins
COPY runtimes/hermes/runtime_skills ./runtime_skills
COPY --from=minimax-skills /source/skills/minimax-docx ./runtime_skills/minimax-docx
COPY --from=minimax-skills /source/skills/minimax-xlsx ./runtime_skills/minimax-xlsx
COPY --from=minimax-skills /source/skills/pptx-generator ./runtime_skills/pptx-generator
COPY --from=minimax-skills /source/skills/minimax-pdf ./runtime_skills/minimax-pdf
COPY autopilots_identity ./autopilots_identity
COPY --from=office-helper /out/office-collaboration /usr/local/bin/office-collaboration
COPY --from=minimax-docx /out/MiniMaxAIDocx.Cli /usr/local/bin/minimax-docx

ENV API_SERVER_ENABLED=true
ENV API_SERVER_HOST=0.0.0.0
ENV API_SERVER_PORT=8642
ENV HERMES_HOME=/data/hermes
ENV HERMES_BUNDLED_SKILLS=/app/runtime_skills
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV PORT=8642

EXPOSE 8642

CMD ["python3", "start_hermes.py"]
