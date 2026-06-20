from __future__ import annotations

import os
import json
import uuid
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
        name="AGUIThroughInvocationsAgent",
        instructions=(
            "You are a concise helpful assistant exposed through Foundry "
            "Hosted Agent Invocations with AG-UI-shaped events."
        ),
        default_options={"store": False},
    )


def sse(event: dict) -> str:
    return f"data: {json.dumps(event, separators=(',', ':'))}\n\n"


def latest_user_text(messages: list[dict]) -> str | None:
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content
    return None


class ResponsesAndInvocationsHost(ResponsesHostServer):
    def __init__(self, agent: Agent) -> None:
        super().__init__(agent)
        invocations = InvocationAgentServerHost()

        @invocations.invoke_handler
        async def handle_invoke(request: Request) -> Response:
            data = await request.json()
            messages = data.get("messages")
            if isinstance(messages, list):
                user_message = latest_user_text(messages)
                if user_message is None:
                    return Response("Missing user message in AG-UI messages", status_code=400)

                thread_id = data.get("threadId")
                if not isinstance(thread_id, str) or not thread_id.strip():
                    thread_id = getattr(request.state, "session_id", str(uuid.uuid4()))
                run_id = data.get("runId")
                if not isinstance(run_id, str) or not run_id.strip():
                    run_id = str(uuid.uuid4())
                message_id = str(uuid.uuid4())
                session = _sessions.setdefault(thread_id, AgentSession(session_id=thread_id))

                async def stream_agui() -> AsyncGenerator[str]:
                    yield sse({"type": "RUN_STARTED", "threadId": thread_id, "runId": run_id})
                    yield sse(
                        {
                            "type": "TEXT_MESSAGE_START",
                            "messageId": message_id,
                            "role": "assistant",
                        }
                    )
                    try:
                        async for update in agent.run(
                            user_message, session=session, stream=True
                        ):
                            if update.text:
                                yield sse(
                                    {
                                        "type": "TEXT_MESSAGE_CONTENT",
                                        "messageId": message_id,
                                        "delta": update.text,
                                    }
                                )
                    except Exception as exc:
                        yield sse({"type": "RUN_ERROR", "message": str(exc)})
                        return
                    yield sse({"type": "TEXT_MESSAGE_END", "messageId": message_id})
                    yield sse({"type": "RUN_FINISHED", "threadId": thread_id, "runId": run_id})

                return StreamingResponse(
                    stream_agui(),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
                )

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


if __name__ == "__main__":
    ResponsesAndInvocationsHost(create_agent()).run()
