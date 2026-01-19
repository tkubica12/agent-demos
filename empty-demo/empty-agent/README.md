# Empty Agent - Teams Bot with OBO Flow

A Teams bot demonstrating the Microsoft 365 Agent SDK with:
- **SSO** (Single Sign-On) with Teams
- **OBO flow to Graph API** for user profile
- **OBO flow to Empty API** for custom data

## How It Works

1. User sends a message in Teams
2. Agent uses SSO to get user's token
3. Agent exchanges token (OBO) for Graph API access → fetches user profile
4. Agent exchanges token (OBO) for Empty API access → fetches custom data
5. Agent responds with combined data from both APIs

## Prerequisites

- Azure Bot Service with OAuth connections configured:
  - `graph` - for Graph API access
  - `emptyapi` - for Empty API access
- Empty API running on `http://localhost:8000`

## Running

```powershell
# Ensure Empty API is running first
cd ../empty-api
uv run uvicorn app:app --host 0.0.0.0 --port 8000 --reload

# Then start the agent (in another terminal)
cd ../empty-agent
uv run python app.py
```

## Configuration

See `.env.example` for required environment variables.

## Commands

| Command | Description |
|---------|-------------|
| `/help` | Show welcome message |
| `/signout` | Sign out from all OAuth connections |
| Any message | Trigger OBO flow to both APIs |
