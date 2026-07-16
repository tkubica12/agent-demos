from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from fastmcp import FastMCP
from starlette.responses import JSONResponse


SERVICE_NAME = "step-10-support-policy-mcp"
DEFAULT_POLICY_PATH = Path(__file__).parent / "policies" / "support-policies.json"

mcp = FastMCP(
    name=SERVICE_NAME,
    instructions=(
        "Read-only Contoso Outdoors support-policy capabilities. "
        "No customer, order, refund, inventory, or shipment mutations are available."
    ),
)


@mcp.tool
def lookup_support_policy(
    topic: Literal["returns", "shipping", "escalation"],
) -> dict[str, object]:
    """Read the authoritative Contoso Outdoors support policy for one topic."""
    policy_path = Path(os.getenv("SUPPORT_POLICY_PATH", str(DEFAULT_POLICY_PATH))).resolve()
    if not policy_path.exists():
        raise RuntimeError(f"SUPPORT_POLICY_PATH does not exist: {policy_path}")
    policies = json.loads(policy_path.read_text(encoding="utf-8"))
    policy = policies.get(topic)
    if not isinstance(policy, dict):
        raise RuntimeError(f"Support policy topic is unavailable: {topic}")
    return {"topic": topic, "policy": policy}


@mcp.custom_route("/health", methods=["GET"])
async def health(_request) -> JSONResponse:
    return JSONResponse(
        {
            "status": "ok",
            "service": SERVICE_NAME,
            "transport": "streamable-http",
            "businessTools": ["lookup_support_policy"],
        }
    )


app = mcp.http_app(
    path="/mcp",
    transport="streamable-http",
    stateless_http=True,
    host_origin_protection=True,
)


def main() -> None:
    mcp.run(
        transport="streamable-http",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        path="/mcp",
    )


if __name__ == "__main__":
    main()
