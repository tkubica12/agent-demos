from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from .core import INSTRUCTIONS, ROOT, TOOLS, ResearchRun


def record_usage(run: ResearchRun, raw: dict[str, Any], origin: str) -> None:
    incoming = raw.get("input_tokens", raw.get("input_token_count", raw.get("input")))
    outgoing = raw.get("output_tokens", raw.get("output_token_count", raw.get("output")))
    if incoming is None or outgoing is None:
        run.log("unrecognized_usage", origin=origin, raw=raw)
        return
    details = raw.get("input_token_details", raw.get("input_tokens_details", {})) or {}
    cached = raw.get("cacheRead", raw.get("cache_read_tokens", raw.get(
        "cache_read_input_token_count", raw.get("input_cached_token_count",
        details.get("cache_read", details.get("cached_tokens", 0)))
    )))
    writes = raw.get("cacheWrite", raw.get("cache_write_tokens", raw.get(
        "cache_creation_input_token_count", raw.get("input_cache_creation_token_count",
        details.get("cache_creation", details.get("cache_write_tokens", 0)))
    )))
    if origin == "pi_assistant_message":
        incoming = int(incoming) + int(cached or 0) + int(writes or 0)
    if min(int(incoming), int(outgoing), int(cached or 0), int(writes or 0)) < 0:
        raise ValueError("Provider usage contains a negative token count.")
    if int(cached or 0) + int(writes or 0) > int(incoming):
        raise ValueError("Cache tokens exceed total input; provider semantics need reconciliation.")
    usage = {
        "input_tokens": int(incoming), "output_tokens": int(outgoing),
        "cached_tokens": int(cached or 0), "cache_write_tokens": int(writes or 0),
        "origin": origin, "raw": raw,
    }
    run.usage.append(usage)
    run.log("usage", **usage)


async def deepagents(run: ResearchRun) -> str:
    from deepagents import HarnessProfile, create_deep_agent, register_harness_profile
    from langchain.agents.middleware import TodoListMiddleware
    from langchain_core.callbacks import BaseCallbackHandler
    from langchain_core.tools import StructuredTool
    from langchain_openai import ChatOpenAI

    class Observer(BaseCallbackHandler):
        raise_error = True
        calls = 0

        def on_chat_model_start(self, *_: Any, **__: Any) -> None:
            self.calls += 1
            if self.calls > run.settings["limits"]["max_model_calls"]:
                raise RuntimeError("CAPPED: native model call limit.")

        def on_llm_end(self, response: Any, **_: Any) -> None:
            for batch in response.generations:
                for generation in batch:
                    usage = getattr(generation.message, "usage_metadata", None)
                    if usage:
                        record_usage(run, usage, "langchain_message")

    model_config = run.settings["model"]
    register_harness_profile(
        f"openai:{model_config['deployment']}",
        HarnessProfile(excluded_tools=frozenset({
            "task", "ls", "read_file", "write_file", "edit_file", "delete", "glob", "grep", "execute",
        })),
    )
    model = ChatOpenAI(
        model=model_config["deployment"], base_url=model_config["base_url"],
        api_key=run.token(), use_responses_api=True, store=False,
        reasoning={"effort": model_config["reasoning_effort"]},
        max_tokens=model_config["max_output_tokens"], max_retries=0, streaming=True,
        callbacks=[Observer()],
    )

    def make_tool(spec: Any) -> Any:
        async def invoke(**arguments: Any) -> str:
            return json.dumps(await run.call(spec.name, arguments), ensure_ascii=False)
        return StructuredTool.from_function(
            coroutine=invoke, name=spec.name, description=spec.description,
            args_schema=spec.parameters,
        )

    agent = create_deep_agent(
        model=model, tools=[make_tool(spec) for spec in TOOLS],
        system_prompt=INSTRUCTIONS, middleware=[TodoListMiddleware()],
    )
    run.log("harness_ready")
    response = await agent.ainvoke(
        {"messages": [{"role": "user", "content": run.prompt()}]},
        config={"recursion_limit": 100},
    )
    return str(response["messages"][-1].content)


async def maf(run: ResearchRun) -> str:
    from agent_framework import FunctionInvocationConfiguration, create_harness_agent, tool
    from agent_framework.openai import OpenAIChatClient
    from openai import AsyncOpenAI

    def make_tool(spec: Any) -> Any:
        async def invoke(**arguments: Any) -> str:
            return json.dumps(await run.call(spec.name, arguments), ensure_ascii=False)
        return tool(invoke, name=spec.name, description=spec.description, schema=spec.parameters)

    model = run.settings["model"]
    async def token() -> str:
        return await asyncio.to_thread(run.token)

    async with AsyncOpenAI(
        base_url=model["base_url"], api_key=token, max_retries=0,
    ) as api:
        client = OpenAIChatClient(
            model=model["deployment"], async_client=api,
            function_invocation_configuration=FunctionInvocationConfiguration(
                max_iterations=run.settings["limits"]["max_model_calls"],
            ),
        )
        agent = create_harness_agent(
            client=client, agent_instructions=INSTRUCTIONS,
            tools=[make_tool(spec) for spec in TOOLS],
            disable_web_search=True, disable_file_memory=True, disable_mode=True,
            max_context_window_tokens=64000, max_output_tokens=model["max_output_tokens"],
            default_options={
                "reasoning": {"effort": model["reasoning_effort"]},
                "max_output_tokens": model["max_output_tokens"],
                "store": False,
            },
        )
        run.log("harness_ready")
        response = await agent.run(run.prompt(), session=agent.create_session())
        if response.usage_details:
            record_usage(run, dict(response.usage_details), "maf_response_aggregate")
        return response.text


async def copilot(run: ResearchRun) -> str:
    from copilot import CopilotClient, RuntimeConnection
    from copilot.tools import Tool, ToolResult

    runtime_root = os.environ.get("COPILOT_CLI_RESOLVED_DIST_DIR")
    runtime = Path(runtime_root) / "prebuilds" / "win32-x64" / "copilot-runtime.exe" if runtime_root else None
    if runtime is None or not runtime.is_file():
        raise RuntimeError("BLOCKED: set COPILOT_CLI_RESOLVED_DIST_DIR to an approved installed runtime.")
    child_env = {
        key: value for key, value in os.environ.items()
        if key in {
            "PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "OS", "PROCESSOR_ARCHITECTURE",
            "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA", "APPDATA",
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
            "http_proxy", "https_proxy", "all_proxy", "no_proxy",
            "NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE", "SSL_CERT_DIR",
        }
    }
    child_env["COPILOT_SKIP_CLI_DOWNLOAD"] = "1"

    def make_tool(spec: Any) -> Tool:
        async def invoke(invocation: Any) -> ToolResult:
            result = await run.call(spec.name, invocation.arguments)
            return ToolResult(text_result_for_llm=json.dumps(result, ensure_ascii=False), result_type="success")
        return Tool(
            name=spec.name, description=spec.description, parameters=spec.parameters,
            handler=invoke, skip_permission=True,
        )

    async def deny_permission(*_: Any) -> dict[str, str]:
        return {"kind": "denied-by-rules"}

    async def bearer_token(*_: Any) -> str:
        return await asyncio.to_thread(run.token)

    event_problem = asyncio.get_running_loop().create_future()

    def on_event(event: Any) -> None:
        if event.type.value == "assistant.usage":
            data = event.data
            raw = {
                key: value for key, value in vars(data).items()
                if value is None or isinstance(value, (int, float, str, bool))
            }
            try:
                record_usage(run, raw, "copilot_assistant_usage")
            except (ValueError, TypeError) as error:
                if not event_problem.done():
                    event_problem.set_result(error)
            if (
                len(run.usage) >= run.settings["limits"]["max_model_calls"]
                and raw.get("finish_reason") not in {"stop", "end_turn"}
                and not event_problem.done()
            ):
                event_problem.set_result(RuntimeError("CAPPED: native model response limit."))
        elif event.type.value in {"session.error", "tool.execution_start", "tool.execution_complete"}:
            run.log("native_event", event_type=event.type.value)

    model = run.settings["model"]
    async with CopilotClient(
        connection=RuntimeConnection.for_stdio(path=str(runtime)),
        mode="empty", working_directory=str(run.directory),
        base_directory=str(run.directory / "copilot-state"), env=child_env,
        use_logged_in_user=False, log_level="error",
    ) as client:
        async with await client.create_session(
            model=model["deployment"], reasoning_effort=model["reasoning_effort"],
            provider={
                "type": "openai", "base_url": model["base_url"], "wire_api": "responses",
                "bearer_token_provider": bearer_token,
                "max_prompt_tokens": 64000, "max_output_tokens": model["max_output_tokens"],
            },
            system_message={"mode": "replace", "content": INSTRUCTIONS},
            tools=[make_tool(spec) for spec in TOOLS],
            available_tools=[spec.name for spec in TOOLS],
            on_permission_request=deny_permission,
            skip_custom_instructions=True, enable_config_discovery=False,
            enable_session_store=False, enable_host_git_operations=False,
            on_event=on_event,
        ) as session:
            run.log("harness_ready")
            pending = asyncio.create_task(session.send_and_wait(
                run.prompt(), timeout=run.settings["limits"]["run_timeout_seconds"],
            ))
            try:
                await asyncio.wait({pending, event_problem}, return_when=asyncio.FIRST_COMPLETED)
                if event_problem.done():
                    await session.abort()
                    raise event_problem.result()
                message = pending.result()
            finally:
                if not pending.done():
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
                event_problem.cancel()
            return message.data.content if message else ""


async def pi(run: ResearchRun) -> str:
    environment = {
        key: value for key, value in os.environ.items()
        if key in {
            "PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "OS", "PROCESSOR_ARCHITECTURE",
            "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA", "APPDATA",
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
            "http_proxy", "https_proxy", "all_proxy", "no_proxy",
            "NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE", "SSL_CERT_DIR",
        }
    }
    process = await asyncio.create_subprocess_exec(
        "node", str(ROOT / "pi.mjs"),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, env=environment, cwd=str(ROOT),
    )
    assert process.stdin and process.stdout and process.stderr
    stderr_task = asyncio.create_task(process.stderr.read())
    initialization = {
        "directory": str(run.directory), "model": run.settings["model"],
        "token": await asyncio.to_thread(run.token), "instructions": INSTRUCTIONS,
        "prompt": run.prompt(),
        "tools": [{"name": s.name, "description": s.description, "parameters": s.parameters} for s in TOOLS],
        "max_model_calls": run.settings["limits"]["max_model_calls"],
    }
    try:
        process.stdin.write((json.dumps(initialization) + "\n").encode())
        await process.stdin.drain()
        while line := await process.stdout.readline():
            message = json.loads(line)
            if message["type"] == "tool":
                result = await run.call(message["name"], message["arguments"])
                process.stdin.write((json.dumps({"id": message["id"], "result": result}) + "\n").encode())
                await process.stdin.drain()
            elif message["type"] == "usage":
                record_usage(run, message["usage"], "pi_assistant_message")
            elif message["type"] == "ready":
                run.log("harness_ready")
            elif message["type"] == "done":
                return message["text"]
            elif message["type"] == "error":
                raise RuntimeError(message["error"])
        await process.wait()
        raise RuntimeError(f"Pi exited {process.returncode}: {(await stderr_task).decode()[:3000]}")
    finally:
        if process.returncode is None:
            process.terminate()
            await process.wait()
        if not stderr_task.done():
            await stderr_task
        errors = stderr_task.result().decode(errors="replace").replace(initialization["token"], "[REDACTED]")
        if errors:
            run.log("pi_stderr", text=errors[:6000])


ADAPTERS = {"deepagents": deepagents, "maf": maf, "copilot": copilot, "pi": pi}
