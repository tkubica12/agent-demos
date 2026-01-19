# start_server.py
"""Server startup module for the Microsoft 365 Agent SDK."""
from aiohttp.web import Application, Request, Response, run_app
from microsoft_agents.hosting.aiohttp import (
    jwt_authorization_middleware,
    CloudAdapter,
    start_agent_process,
)


async def health(request: Request) -> Response:
    """Health check endpoint."""
    return Response(text="OK")


def start_server(agent_application, auth_configuration, port: int = 3978):
    """Start the aiohttp server for the bot.
    
    Args:
        agent_application: The AgentApplication instance
        auth_configuration: Authentication configuration (can be None for local testing)
        port: Server port (default 3978)
    """
    print(f"Starting server on http://localhost:{port}")
    print("Listening for messages on /api/messages")
    
    async def entry_point(req: Request) -> Response:
        """Handle incoming bot activities."""
        agent = req.app["agent_app"]
        adapter = req.app["adapter"]
        return await start_agent_process(req, agent, adapter)
    
    # Create app with JWT middleware if auth is configured
    if auth_configuration:
        APP = Application(middlewares=[jwt_authorization_middleware])
    else:
        APP = Application()
    
    # Register routes
    APP.router.add_post("/api/messages", entry_point)
    APP.router.add_get("/api/messages", lambda _: Response(status=200))
    APP.router.add_get("/health", health)
    
    # Store configuration in app context
    APP["agent_configuration"] = auth_configuration
    APP["agent_app"] = agent_application
    APP["adapter"] = agent_application.adapter
    
    run_app(APP, host="localhost", port=port)
