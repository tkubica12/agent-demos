from __future__ import annotations

import json
from pathlib import Path

from fastmcp import Client

from support_policy_mcp import mcp


async def main() -> None:
    async with Client(mcp) as client:
        tools = await client.list_tools()
        assert [tool.name for tool in tools] == ["lookup_support_policy"]
        assert all(
            fragment not in tool.name.lower()
            for tool in tools
            for fragment in ("write", "update", "delete", "refund")
        )
        result = await client.call_tool("lookup_support_policy", {"topic": "returns"})
        payload = json.loads(result.content[0].text)
        assert payload["policy"]["policy_id"] == "RET-CANARY-9031"

    v1 = (Path("toolbox/skills/v1/references/escalation-matrix.md")).read_text()
    v2 = (Path("toolbox/skills/v2/references/escalation-matrix.md")).read_text()
    assert "ESC-CANARY-7742" in v1
    assert "ESC-CANARY-9921" in v2
    assert "10 business days" not in v1
    assert "10 business days" in v2


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
