from __future__ import annotations

import os
from collections.abc import AsyncGenerator

from agent_framework import Agent, AgentSession
from agent_framework.foundry import FoundryChatClient
from agent_framework_ag_ui import add_agent_framework_fastapi_endpoint
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.ai.agentserver.invocations import InvocationAgentServerHost
from azure.identity import DefaultAzureCredential
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse


DEFAULT_MODEL_DEPLOYMENT = "gpt-5.4-mini"
WEB_DIR = os.path.join(os.path.dirname(__file__), "web")
_sessions: dict[str, AgentSession] = {}


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} must be set.")
    return value


def create_agent() -> Agent:
    client = FoundryChatClient(
        project_endpoint=required_env("FOUNDRY_PROJECT_ENDPOINT"),
        model=os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", DEFAULT_MODEL_DEPLOYMENT),
        credential=DefaultAzureCredential(),
    )

    return Agent(
        client=client,
        name="AGUILocalAdapterAgent",
        instructions=(
            "You are a concise helpful assistant exposed locally through AG-UI "
            "and backed by a Microsoft Foundry model."
        ),
        default_options={"store": False},
    )


class ResponsesAndInvocationsHost(ResponsesHostServer):
    def __init__(self, agent: Agent) -> None:
        super().__init__(agent)
        invocations = InvocationAgentServerHost()

        @invocations.invoke_handler
        async def handle_invoke(request: Request) -> Response:
            data = await request.json()
            stream = data.get("stream", False)
            user_message = data.get("message")
            if not isinstance(user_message, str) or not user_message.strip():
                return Response("Missing non-empty 'message' in request", status_code=400)

            session_id = request.state.session_id
            session = _sessions.setdefault(session_id, AgentSession(session_id=session_id))

            if stream:

                async def stream_response() -> AsyncGenerator[str]:
                    async for update in agent.run(
                        user_message, session=session, stream=True
                    ):
                        if update.text:
                            yield update.text

                return StreamingResponse(
                    stream_response(),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
                )

            response = await agent.run([user_message], session=session, stream=False)
            return JSONResponse({"response": response.text})

        for route in invocations.routes:
            if getattr(route, "path", "").startswith("/invocations"):
                self.router.routes.append(route)


def create_app() -> FastAPI:
    app = FastAPI(title="Step 04 AG-UI local adapter")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    add_agent_framework_fastapi_endpoint(app, create_agent(), "/agui")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(os.path.join(WEB_DIR, "index.html"))

    app.mount("/web", StaticFiles(directory=WEB_DIR), name="web")
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8088)
