from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from azure.identity import DefaultAzureCredential
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ValidationError


APP_PORT = int(os.getenv("PORT", "8088"))
DEFAULT_HERMES_HOME = "/files/hermes" if Path("/files").exists() else ".hermes"
logger = logging.getLogger("hermes_foundry_gateway")


class InvocationRequest(BaseModel):
    input: str | dict[str, Any] | list[Any] | None = None
    task: str | None = None
    session_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResponsesRequest(BaseModel):
    input: str | list[Any] | dict[str, Any]
    conversation: str | dict[str, Any] | None = None
    conversation_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    stream: bool | None = None


@dataclass(frozen=True)
class HermesResult:
    text: str
    returncode: int
    duration_ms: int


class HermesRunner:
    def __init__(self) -> None:
        self.executable = os.getenv("HERMES_EXECUTABLE", "hermes")
        self.home = os.getenv("HERMES_HOME", DEFAULT_HERMES_HOME)
        self.timeout_seconds = int(os.getenv("HERMES_TURN_TIMEOUT_SECONDS", "900"))
        self.foundry_base_url = os.getenv("AZURE_FOUNDRY_BASE_URL", "").rstrip("/")
        self.model_deployment = os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "").strip()
        self.use_foundry_token_auth = (
            os.getenv("HERMES_AZURE_FOUNDRY_TOKEN_AUTH", "false").lower()
            in {"1", "true", "yes"}
        )
        self._credential: DefaultAzureCredential | None = None

    async def run_turn(self, prompt: str, session_id: str | None = None) -> HermesResult:
        if not prompt.strip():
            raise ValueError("Prompt must not be empty.")

        env = os.environ.copy()
        env["HERMES_HOME"] = self.home
        Path(self.home).mkdir(parents=True, exist_ok=True)
        self._ensure_hermes_config()

        if self.use_foundry_token_auth:
            env["AZURE_FOUNDRY_API_KEY"] = await asyncio.to_thread(
                self._get_foundry_access_token
            )

        command = self._command(prompt)
        if session_id:
            env["HERMES_FOUNDRY_SESSION_ID"] = session_id

        started = time.monotonic()
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError as error:
            raise RuntimeError(
                f"Hermes command '{' '.join(command)}' was not found."
            ) from error
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.timeout_seconds
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            raise TimeoutError(
                f"Hermes turn exceeded {self.timeout_seconds} seconds."
            ) from None

        duration_ms = int((time.monotonic() - started) * 1000)
        stdout_text = stdout.decode("utf-8", errors="replace").strip()
        stderr_text = stderr.decode("utf-8", errors="replace").strip()
        combined_text = "\n".join(part for part in (stdout_text, stderr_text) if part)

        if process.returncode != 0 or self._is_configuration_failure(combined_text):
            details = combined_text or "Hermes exited without output."
            raise RuntimeError(f"Hermes failed with exit code {process.returncode}: {details}")

        return HermesResult(
            text=self._clean_hermes_output(stdout_text),
            returncode=process.returncode or 0,
            duration_ms=duration_ms,
        )

    def _command(self, prompt: str) -> list[str]:
        if self.executable == "hermes":
            return [sys.executable, "-m", "hermes_cli.main", "chat", "-q", prompt]
        resolved = shutil.which(self.executable)
        if resolved:
            return [resolved, "chat", "-q", prompt]
        return [self.executable, "chat", "-q", prompt]

    @staticmethod
    def _is_configuration_failure(output: str) -> bool:
        failure_markers = (
            "No inference provider configured",
            "Run 'hermes model' to choose a provider",
            "AuthenticationError [HTTP 401]",
            "PermissionDenied",
            "Your API key was rejected by the provider",
            "Principal does not have access to API/Operation",
            "lacks the required data action",
        )
        return any(marker in output for marker in failure_markers)

    def _ensure_hermes_config(self) -> None:
        if not self.foundry_base_url or not self.model_deployment:
            return

        home = Path(self.home)
        home.mkdir(parents=True, exist_ok=True)
        config_path = home / "config.yaml"
        config: dict[str, Any] = {}
        if config_path.exists():
            loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            if isinstance(loaded, dict):
                config = loaded

        model_config = config.get("model")
        if not isinstance(model_config, dict):
            model_config = {}
        model_config.update(
            {
                "provider": "azure-foundry",
                "default": self.model_deployment,
                "base_url": self.foundry_base_url,
                "api_mode": "chat_completions",
            }
        )
        config["model"] = model_config
        config.setdefault("toolsets", ["hermes-cli"])
        config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    def _get_foundry_access_token(self) -> str:
        if self._credential is None:
            self._credential = DefaultAzureCredential()
        return self._credential.get_token("https://cognitiveservices.azure.com/.default").token

    @staticmethod
    def _clean_hermes_output(output: str) -> str:
        output = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", output)
        content: list[str] = []
        skip_prefixes = (
            "Query:",
            "Initializing agent",
            "Resume this session with:",
            "hermes --resume",
            "Session:",
            "Duration:",
            "Messages:",
            "Goodbye!",
            "⚠ tirith security scanner",
            "⚠ Ignoring inherited PYTHONPATH",
            "⚠ Linux detected.",
            "✓ Detected:",
            "✓ Node.js",
            "✓ Chrome",
            "→ Checking Node.js",
            "→ Node.js",
            "→ Downloading node",
            "→ Extracting to",
            "→ Installing",
            "Installing Chrome",
            "Downloading Chrome",
            "https://storage.googleapis.com/",
            "Location:",
            "Note:",
            "agent-browser install",
        )
        for raw_line in output.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith(skip_prefixes):
                continue
            if "Hermes" in line and ("─" in line or "╭" in line or "╮" in line):
                continue
            if set(line) <= {"─", "╭", "╮", "╰", "╯", "│", " "}:
                continue
            content.append(line)
        return "\n".join(content).strip() or output.strip()


def extract_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            return value["text"]
        if isinstance(value.get("content"), str):
            return value["content"]
        if isinstance(value.get("content"), list):
            return extract_text(value["content"])
        if isinstance(value.get("input"), str):
            return value["input"]
        if isinstance(value.get("input"), list):
            return extract_text(value["input"])
        return "\n".join(f"{key}: {extract_text(val)}" for key, val in value.items())
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict) and item.get("type") in {
                "input_text",
                "message",
                "text",
            }:
                parts.append(extract_text(item.get("text") or item.get("content")))
            else:
                parts.append(extract_text(item))
        return "\n".join(part for part in parts if part)
    return str(value)


def extract_conversation_id(payload: ResponsesRequest) -> str | None:
    if payload.conversation_id:
        return payload.conversation_id
    if isinstance(payload.conversation, str):
        return payload.conversation
    if isinstance(payload.conversation, dict) and isinstance(
        payload.conversation.get("id"), str
    ):
        return payload.conversation["id"]
    return None


async def read_body(request: Request) -> Any:
    try:
        return await request.json()
    except ValueError:
        return (await request.body()).decode("utf-8", errors="replace")


def response_payload(text: str, conversation_id: str | None = None) -> dict[str, Any]:
    response_id = f"resp_{uuid.uuid4().hex}"
    message_id = f"msg_{uuid.uuid4().hex}"
    return {
        "id": response_id,
        "object": "response",
        "created_at": int(time.time()),
        "status": "completed",
        "model": "hermes-agent",
        "conversation": conversation_id,
        "output_text": text,
        "output": [
            {
                "id": message_id,
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": text,
                        "annotations": [],
                    }
                ],
            }
        ],
        "usage": {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        },
    }


def response_stream_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    output = payload["output"][0]
    content = output["content"][0]
    return [
        {"type": "response.created", "response": {**payload, "status": "in_progress"}},
        {"type": "response.output_item.added", "output_index": 0, "item": output},
        {
            "type": "response.content_part.added",
            "item_id": output["id"],
            "output_index": 0,
            "content_index": 0,
            "part": content,
        },
        {
            "type": "response.output_text.delta",
            "item_id": output["id"],
            "output_index": 0,
            "content_index": 0,
            "delta": payload["output_text"],
        },
        {
            "type": "response.output_text.done",
            "item_id": output["id"],
            "output_index": 0,
            "content_index": 0,
            "text": payload["output_text"],
        },
        {
            "type": "response.content_part.done",
            "item_id": output["id"],
            "output_index": 0,
            "content_index": 0,
            "part": content,
        },
        {"type": "response.output_item.done", "output_index": 0, "item": output},
        {"type": "response.completed", "response": payload},
    ]


async def stream_response_payload(payload: dict[str, Any]):
    for event in response_stream_events(payload):
        event_type = event["type"]
        yield f"event: {event_type}\n"
        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    yield "data: [DONE]\n\n"


app = FastAPI(title="Hermes Foundry Gateway", version="0.1.0")
runner = HermesRunner()


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "hermes_home": runner.home,
        "hermes_executable": runner.executable,
    }


@app.get("/readiness")
async def readiness() -> dict[str, str]:
    return {"status": "ready"}


@app.post("/invocations")
async def invocations(request: Request) -> dict[str, Any]:
    body = await read_body(request)
    if isinstance(body, str):
        payload = InvocationRequest(input=body)
    elif isinstance(body, dict):
        try:
            payload = InvocationRequest.model_validate(body)
        except ValidationError as error:
            raise HTTPException(status_code=400, detail=error.errors()) from error
    else:
        payload = InvocationRequest(input=body)

    session_id = payload.session_id or request.headers.get("x-ms-agent-session-id")
    prompt = payload.task or extract_text(payload.input)
    try:
        result = await runner.run_turn(prompt, session_id=session_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except TimeoutError as error:
        logger.warning("Hermes invocation timed out: %s", error)
        raise HTTPException(status_code=504, detail=str(error)) from error
    except RuntimeError as error:
        logger.error("Hermes invocation failed: %s", error)
        raise HTTPException(status_code=502, detail=str(error)) from error

    return {
        "status": "completed",
        "session_id": session_id,
        "output": result.text,
        "duration_ms": result.duration_ms,
        "metadata": payload.metadata,
    }


@app.post("/responses")
async def responses(request: Request):
    body = await read_body(request)
    if isinstance(body, str):
        payload = ResponsesRequest(input=body)
    elif isinstance(body, dict):
        try:
            payload = ResponsesRequest.model_validate(body)
        except ValidationError as error:
            raise HTTPException(status_code=400, detail=error.errors()) from error
    else:
        payload = ResponsesRequest(input=body)

    session_id = (
        extract_conversation_id(payload)
        or request.headers.get("x-ms-agent-session-id")
    )
    prompt = extract_text(payload.input)
    try:
        result = await runner.run_turn(prompt, session_id=session_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except TimeoutError as error:
        logger.warning("Hermes response timed out: %s", error)
        raise HTTPException(status_code=504, detail=str(error)) from error
    except RuntimeError as error:
        logger.error("Hermes response failed: %s", error)
        raise HTTPException(status_code=502, detail=str(error)) from error

    response = response_payload(result.text, conversation_id=session_id)
    if payload.stream:
        return StreamingResponse(
            stream_response_payload(response),
            media_type="text/event-stream",
        )
    return response


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    logging.getLogger("azure").setLevel(logging.WARNING)
    uvicorn.run("main:app", host="0.0.0.0", port=APP_PORT)
