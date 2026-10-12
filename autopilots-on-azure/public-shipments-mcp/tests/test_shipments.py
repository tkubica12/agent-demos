import asyncio
import importlib

from fastmcp import Client

from public_shipments_mcp.shipments import shipment_ids, shipment_status


def test_demo_shipments_are_stable() -> None:
    assert shipment_ids() == ["SHIP-1001", "SHIP-1002", "SHIP-1003"]
    assert shipment_status("SHIP-1003")["status"] == "delayed"


def test_auth_mode_none_lists_tools_without_token(monkeypatch) -> None:
    monkeypatch.setenv("MCP_AUTH_MODE", "none")
    import public_shipments_mcp.server as server

    server = importlib.reload(server)

    async def list_tool_names() -> list[str]:
        async with Client(server.mcp) as client:
            return [tool.name for tool in await client.list_tools()]

    assert asyncio.run(list_tool_names())
