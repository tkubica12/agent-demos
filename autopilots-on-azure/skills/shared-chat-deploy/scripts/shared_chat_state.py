"""Print what the shared chat deployment recorded on disk. Reads files only; prints no secrets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import tomllib


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def shared_chat_state(config_path: Path, state_root: Path) -> dict:
    inputs = tomllib.loads(config_path.read_text(encoding="utf-8"))
    chat = inputs.get("shared_chat")
    if not chat:
        raise ValueError("The inputs have no [shared_chat] block.")
    names = [stack["name"] for stack in inputs.get("stacks", [])]
    if chat["host"] not in names:
        raise ValueError("shared_chat.host must name one of the stacks.")

    def root(name: str) -> Path:
        return state_root / f"simulator-{inputs['environment']}-{name}"

    host = root(chat["host"])
    url = read(host / "hosting.json").get("url", "")
    registered = read(host / "registrations.json")
    stacks = {}
    for name in names:
        binding = read(root(name) / "bindings.json").get("agents", {}).get(name, {})
        deployed = read(root(name) / "deployment.json").get("url", "")
        stacks[name] = {
            "gateway_identity_principal_id": binding.get("identity_principal_id", ""),
            "runtime_identity_principal_id": binding.get("runtime_identity_principal_id", ""),
            "registered": name in registered,
            "registered_client_id": registered.get(name, {}).get("clientId", ""),
            "stale_chat_url": bool(deployed) and deployed != url,
        }
    return {
        "host": chat["host"],
        "channel_identity_principal_id": read(host / "bindings.json").get("channel", {}).get(
            "identity_principal_id", "",
        ),
        "redirect_uri": url + "/" if url else "",
        "stacks": stacks,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="The private stack inputs (TOML).")
    parser.add_argument("--state-root", type=Path, required=True, help="The durable deployment state directory.")
    args = parser.parse_args()
    print(json.dumps(shared_chat_state(args.config, args.state_root.expanduser().resolve()), indent=2))


if __name__ == "__main__":
    main()
