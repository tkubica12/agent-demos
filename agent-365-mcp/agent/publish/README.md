# Agent 365 Simple Agent

A simple static Agent 365 agent that echoes user messages. Built with the Microsoft 365 Agents SDK and **FastAPI**.

## Features

- 👋 Welcome message when the bot joins a conversation
- 💬 Echoes back any message with "Hello, this is your agent! You said: ..."
- ❓ `/help` command for basic information
- 🚀 Modern FastAPI + Uvicorn web server

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
