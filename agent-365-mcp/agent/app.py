# app.py
"""
Simple Agent 365 agent using FastAPI.
Echoes user messages with "Hello, this is your agent! You said: <message>"
"""

import sys
import traceback
from os import environ
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
import uvicorn

# Microsoft 365 Agents SDK imports
from microsoft_agents.hosting.core import (
    AgentApplication,
    TurnState,
    TurnContext,
    MemoryStorage,
    Authorization,
)
from microsoft_agents.hosting.fastapi import start_agent_process, CloudAdapter
from microsoft_agents.authentication.msal import MsalConnectionManager
from microsoft_agents.activity import load_configuration_from_env

# Load environment variables from .env file
load_dotenv()

# Load SDK configuration from environment variables
agents_sdk_config = load_configuration_from_env(environ)

# Create storage, connection manager, adapter, and authorization
STORAGE = MemoryStorage()
CONNECTION_MANAGER = MsalConnectionManager(**agents_sdk_config)
ADAPTER = CloudAdapter(connection_manager=CONNECTION_MANAGER)
AUTHORIZATION = Authorization(STORAGE, CONNECTION_MANAGER, **agents_sdk_config)

# Create the Agent Application
AGENT_APP = AgentApplication[TurnState](
    storage=STORAGE,
    adapter=ADAPTER,
    authorization=AUTHORIZATION,
    **agents_sdk_config
)

# Create FastAPI app
app = FastAPI(
    title="Agent 365 Simple Agent",
    description="A simple echo agent built with Microsoft 365 Agents SDK and FastAPI",
    version="1.0.0"
)


@AGENT_APP.conversation_update("membersAdded")
async def on_members_added(context: TurnContext, _state: TurnState):
    """Send a welcome message when the bot joins a conversation."""
    await context.send_activity(
        "👋 Welcome! I'm a simple Agent 365 agent. "
        "Send me any message and I'll echo it back to you. "
        "Type /help for more information."
    )
    return True


@AGENT_APP.message("/help")
async def on_help(context: TurnContext, _state: TurnState):
    """Send help information."""
    await context.send_activity(
        "🤖 **Agent 365 Simple Agent**\n\n"
        "I'm a basic echo agent built with the Microsoft 365 Agents SDK and FastAPI.\n\n"
        "**Commands:**\n"
        "• `/help` - Show this help message\n"
        "• Any other message - I'll echo it back to you!\n\n"
        "This agent demonstrates the basic structure of an Agent 365 agent."
    )


@AGENT_APP.activity("message")
async def on_message(context: TurnContext, _state: TurnState):
    """Handle incoming messages - echo them back with a friendly prefix."""
    user_text = context.activity.text or ""
    response = f"Hello, this is your agent! You said: {user_text}"
    await context.send_activity(response)


@AGENT_APP.error
async def on_error(context: TurnContext, error: Exception):
    """Handle errors."""
    print(f"\n [on_turn_error] unhandled error: {error}", file=sys.stderr)
    traceback.print_exc()
    await context.send_activity("The bot encountered an error or bug.")


# FastAPI Routes
@app.get("/")
async def root():
    """Health check endpoint."""
    return PlainTextResponse("Agent 365 Simple Agent is running!")


@app.get("/api/messages")
async def messages_get():
    """Health check for messages endpoint."""
    return PlainTextResponse("OK")


@app.post("/api/messages")
async def messages(request: Request):
    """Handle incoming agent messages."""
    return await start_agent_process(request, AGENT_APP, ADAPTER)


if __name__ == "__main__":
    # Get port from environment (Azure uses PORT, default to 8000)
    port = int(environ.get("PORT", environ.get("WEBSITES_PORT", 8000)))
    host = environ.get("HOST", "0.0.0.0")
    
    print(f"🚀 Starting Agent 365 Simple Agent (FastAPI)")
    print(f"📬 Messages endpoint: http://{host}:{port}/api/messages")
    
    uvicorn.run(app, host=host, port=port)
