"""Test client for the PromoServer MCP server."""

import asyncio
import sys
from fastmcp import Client


async def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000/mcp"
    print(f"Connecting to MCP server at {url}")

    client = Client(url)

    async with client:
        print("Connected!\n")

        # List tools
        tools = await client.list_tools()
        print("Available tools:")
        for tool in tools:
            print(f"  - {tool.name}: {tool.description}")

        # Call getPromo
        print("\nCalling getPromo...")
        result = await client.call_tool("getPromo", {})
        print(f"Promo result: {result}")


if __name__ == "__main__":
    asyncio.run(main())
