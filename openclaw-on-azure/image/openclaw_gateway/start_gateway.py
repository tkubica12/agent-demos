from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import sys
import time

from openclaw_gateway.bootstrap import ensure_openclaw_files, openclaw_env


def start_foundry_proxy(env: dict[str, str]) -> subprocess.Popen | None:
    if not env.get("FOUNDRY_OPENAI_BASE_URL"):
        print("FOUNDRY_OPENAI_BASE_URL is not set; Foundry managed-identity proxy is disabled.", flush=True)
        return None
    proxy_port = env.get("FOUNDRY_PROXY_PORT", "18080")
    print(f"Starting Foundry managed-identity proxy on 127.0.0.1:{proxy_port}", flush=True)
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "openclaw_gateway.foundry_token_proxy:app",
            "--host",
            "127.0.0.1",
            "--port",
            proxy_port,
        ],
        env=env,
    )


def main() -> None:
    token = os.getenv("OPENCLAW_GATEWAY_TOKEN")
    if not token:
        token = secrets.token_urlsafe(32)
        print(f"Generated OPENCLAW_GATEWAY_TOKEN={token}", flush=True)
    if os.getenv("FOUNDRY_OPENAI_BASE_URL"):
        os.environ.setdefault("OPENCLAW_MODEL_BASE_URL", f"http://127.0.0.1:{os.getenv('FOUNDRY_PROXY_PORT', '18080')}/v1")
        os.environ.setdefault("OPENCLAW_MODEL_API_KEY", "unused-managed-identity-token-proxy")
    paths = ensure_openclaw_files(gateway=True)
    env = openclaw_env()
    env["OPENCLAW_GATEWAY_TOKEN"] = token
    env.setdefault("PORT", os.getenv("PORT", "18789"))
    if env.get("FOUNDRY_OPENAI_BASE_URL"):
        env.setdefault("OPENCLAW_MODEL_BASE_URL", f"http://127.0.0.1:{env.get('FOUNDRY_PROXY_PORT', '18080')}/v1")
        env.setdefault("OPENCLAW_MODEL_API_KEY", "unused-managed-identity-token-proxy")
    print(f"OpenClaw config: {paths['configPath']}", flush=True)
    print(f"OpenClaw workspace: {paths['workspaceDir']}", flush=True)
    openclaw = shutil.which("openclaw") or shutil.which("openclaw.cmd")
    if not openclaw:
        raise RuntimeError("OpenClaw CLI was not found. Install it with: npm install -g openclaw@latest")
    proxy = start_foundry_proxy(env)
    try:
        if proxy:
            time.sleep(2)
        subprocess.run([openclaw, "gateway", "--port", env["PORT"], "--verbose"], env=env, check=True)
    finally:
        if proxy and proxy.poll() is None:
            proxy.terminate()


if __name__ == "__main__":
    main()
