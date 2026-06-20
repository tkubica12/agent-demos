from __future__ import annotations

import os
from collections.abc import AsyncGenerator

from agent_framework import Agent, AgentSession
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.ai.agentserver.invocations import InvocationAgentServerHost
from azure.identity import DefaultAzureCredential
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse


DEFAULT_MODEL_DEPLOYMENT = "gpt-5.4-mini"
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
        name="AGUIAuthenticatedGatewayAgent",
        instructions=(
            "You are a concise helpful assistant running as a Foundry Hosted "
            "Agent behind an authenticated AG-UI gateway."
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

            requested_session_id = data.get("threadId") or data.get("sessionId")
            if isinstance(requested_session_id, str) and requested_session_id.strip():
                session_id = requested_session_id
            else:
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


if __name__ == "__main__":
    ResponsesAndInvocationsHost(create_agent()).run()
