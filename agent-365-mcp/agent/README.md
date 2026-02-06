# Agent 365 Simple Agent

A simple static Agent 365 agent that echoes user messages. Built with the Microsoft 365 Agents SDK and **FastAPI**.

## Architecture note (Tooling Gateway / MCP)

This project is intended to evolve toward the Agent 365 **tooling gateway** model:

- The agent should **not** call line-of-business APIs directly.
- Instead, expose those APIs as **tools** via one (or more) **MCP servers**, and have the agent invoke them through the Agent 365 tooling gateway so calls are governed (permissions/policy/observability).

Relevant docs:

- Agent 365 tooling servers overview: https://learn.microsoft.com/en-us/microsoft-agent-365/tooling-servers-overview
- Add and manage tools (MCP integration workflow): https://learn.microsoft.com/en-us/microsoft-agent-365/developer/tooling

## Features

- 👋 Welcome message when the bot joins a conversation
- 💬 Echoes back any message with "Hello, this is your agent! You said: ..."
- ❓ `/help` command for basic information
- 🚀 Modern FastAPI + Uvicorn web server

## Current status

- Works end-to-end as a basic echo agent (blueprint deployed and instance created)
- Tooling/MCP integration is not implemented yet in code (no tool calls; no downstream API calls)

## Prerequisites

- Python 3.11+
- [uv package manager](https://pypi.org/project/uv/) - Install with `pip install uv`
- [a365 CLI](https://learn.microsoft.com/en-us/microsoft-agent-365/developer/agent-365-cli) for deployment

## Quick Start

### 1. Install Dependencies

```bash
uv sync
```

### 2. Run the Agent Locally

```bash
uv run python app.py
```

The agent will start on `http://localhost:3978`.

### 3. Test with Agents Playground

In another terminal, install and run the test tool:

```bash
npm install -g @microsoft/teams-app-test-tool
teamsapptester
```

This opens a browser where you can chat with your agent.

## Deployment with a365 CLI

### 1. Initialize Configuration (already done)

```bash
a365 config init
```

### 2. Deploy the Agent

```bash
a365 deploy
```

### 3. Publish to Microsoft 365

```bash
a365 publish
```

### 4. Add bot based notification configuration

[https://learn.microsoft.com/en-us/microsoft-agent-365/developer/create-instance](https://learn.microsoft.com/en-us/microsoft-agent-365/developer/create-instance)

Then user can request an instance, get approval, and the agent shows up in Teams.

## Next steps (WIP)

- Configure MCP servers with `a365` tooling commands (generates a `ToolingManifest.json`) and integrate tool invocation into the agent.
- Use agent identity / delegated user access only through the tooling gateway (avoid hard-coding API tokens or doing direct REST calls to protected APIs from the agent).

## Project Structure

```
agent/
├── a365.config.json    # Agent 365 CLI configuration
├── app.py              # Main agent application (FastAPI + Uvicorn)
├── pyproject.toml      # Python dependencies (uv)
├── requirements.txt    # Dependencies for Azure App Service
├── .env.template       # Environment variables template
└── README.md           # This file
```

## SDK References

- [Microsoft 365 Agents SDK](https://github.com/microsoft/Agents-for-python)
- [Agent 365 Developer Docs](https://learn.microsoft.com/en-us/microsoft-agent-365/developer/)
- [Quickstart Guide](https://learn.microsoft.com/en-us/microsoft-365/agents-sdk/quickstart)
