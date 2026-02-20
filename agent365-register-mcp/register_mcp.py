"""
Register a custom MCP server with Microsoft Agent 365 Tooling Gateway.

Uses the MCPManagement MCP server to:
1. Create a new MCP server entry in the Dataverse environment
2. Discover tools from the remote MCP server
3. Register tools via UpdateTool (set description and inputSchema)
4. Publish the server to the tenant

Prerequisites:
  - a365 CLI installed and authenticated
  - a365.config.json in the agent365-agentframework-python directory
  - User must have System Administrator role in Dataverse environment

Usage:
    uv run python register_mcp.py
"""

import asyncio
import json
import os
import subprocess
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

# Configuration
ENVIRONMENT_ID = "Default-6ce4f237-667f-43f5-aafd-cbef954adf97"
MCPMANAGEMENT_URL = f"https://agent365.svc.cloud.microsoft/mcp/environments/{ENVIRONMENT_ID}/servers/MCPManagement"

# Dataverse requires customization prefix; "new_" is the default publisher prefix
CUSTOM_SERVER_NAME = "new_PromoMCPServer"
CUSTOM_SERVER_DISPLAY_NAME = "Promo MCP Server"
CUSTOM_SERVER_DESCRIPTION = "Custom MCP server for promo scenarios running on Azure Container Apps"

REMOTE_MCP_URL = "https://promo-mcp-server.gentlefield-413fdf4c.swedencentral.azurecontainerapps.io/mcp"

A365_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "..", "agent365-agentframework-python", "a365.config.json"
)


def get_token() -> str:
    """Get OAuth token for MCPManagement server using a365 CLI."""
    token = os.environ.get("MCP_MANAGEMENT_TOKEN")
    if token:
        print("[INFO] Using token from MCP_MANAGEMENT_TOKEN environment variable")
        return token

    print("[INFO] Acquiring token via a365 CLI...")
    try:
        result = subprocess.run(
            ["a365", "develop", "get-token", "--scopes", "McpServers.Management.All", "-o", "raw", "-c", A365_CONFIG_PATH],
            capture_output=True, text=True, timeout=120,
        )
        for line in reversed(result.stdout.strip().splitlines()):
            line = line.strip()
            if line.startswith("eyJ"):
                return line
        print(f"[ERROR] Could not extract token from a365 output:\n{result.stdout}")
        sys.exit(1)
    except FileNotFoundError:
        print("[ERROR] a365 CLI not found. Install it or set MCP_MANAGEMENT_TOKEN env var.")
        sys.exit(1)


def extract_text(result) -> str:
    """Extract text content from MCP tool call result."""
    texts = [c.text for c in result.content if hasattr(c, "text")]
    return "\n".join(texts)


def parse_json_response(result) -> dict | None:
    """Parse the first JSON object from MCP tool call result."""
    for c in result.content:
        if hasattr(c, "text"):
            try:
                return json.loads(c.text)
            except json.JSONDecodeError:
                continue
    return None


async def discover_remote_tools(url: str) -> list[dict]:
    """Connect to the remote MCP server and discover its tools."""
    try:
        async with streamablehttp_client(url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                return [
                    {"name": t.name, "description": t.description, "inputSchema": t.inputSchema}
                    for t in result.tools
                ]
    except Exception as e:
        print(f"[WARN] Could not connect to remote MCP server: {e}")
        return []


async def run():
    token = get_token()
    print(f"[INFO] Token acquired (length={len(token)})")
    print(f"[INFO] Connecting to MCPManagement at: {MCPMANAGEMENT_URL}")

    headers = {"Authorization": f"Bearer {token}"}

    async with streamablehttp_client(MCPMANAGEMENT_URL, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("[OK] Connected to MCPManagement server\n")

            # Step 1: Check if server already exists
            print("=== Step 1: Checking existing MCP servers ===")
            existing = parse_json_response(await session.call_tool("GetMCPServers", {}))
            server_exists = False
            if existing and "servers" in existing:
                for s in existing["servers"]:
                    if s.get("mcpServerName") == CUSTOM_SERVER_NAME:
                        server_exists = True
                        print(f"  Server '{CUSTOM_SERVER_NAME}' already exists (id={s.get('id')})")
                        break

            # Step 2: Create server if it doesn't exist
            if not server_exists:
                print(f"\n=== Step 2: Creating MCP server '{CUSTOM_SERVER_NAME}' ===")
                create_result = parse_json_response(await session.call_tool("CreateMCPServer", {
                    "serverName": CUSTOM_SERVER_NAME,
                    "displayName": CUSTOM_SERVER_DISPLAY_NAME,
                    "description": CUSTOM_SERVER_DESCRIPTION,
                }))
                if create_result and "error" not in str(create_result).lower():
                    print(f"  [OK] Server created: {json.dumps(create_result, indent=2)}")
                else:
                    print(f"  [ERROR] {json.dumps(create_result, indent=2)}")
                    return
            else:
                print("  Skipping creation - server already exists.")

            # Step 3: Discover tools from the remote MCP server
            print(f"\n=== Step 3: Discovering tools from remote MCP server ===")
            print(f"  URL: {REMOTE_MCP_URL}")
            remote_tools = await discover_remote_tools(REMOTE_MCP_URL)
            if remote_tools:
                print(f"  Found {len(remote_tools)} tool(s):")
                for t in remote_tools:
                    print(f"    - {t['name']}: {t.get('description', 'N/A')}")
            else:
                print("  [WARN] No tools discovered from remote server")

            # Step 4: Get server details
            print(f"\n=== Step 4: Server details ===")
            server_info = parse_json_response(await session.call_tool("GetMCPServer", {
                "mcpServerName": CUSTOM_SERVER_NAME,
            }))
            print(f"  {json.dumps(server_info, indent=2)}")

            # Step 5: List current tools on the server
            print(f"\n=== Step 5: Current tools on server ===")
            tools_result = parse_json_response(await session.call_tool("GetTools", {
                "mcpServerName": CUSTOM_SERVER_NAME,
            }))
            print(f"  {json.dumps(tools_result, indent=2)}")

            # Step 6: Publish the server to tenant scope
            print(f"\n=== Step 6: Publishing server to tenant ===")
            publish_result = await session.call_tool("PublishMCPServer", {
                "envId": ENVIRONMENT_ID,
                "mcpServerName": CUSTOM_SERVER_NAME,
                "alias": CUSTOM_SERVER_NAME,
                "displayName": CUSTOM_SERVER_DISPLAY_NAME,
            })
            publish_data = parse_json_response(publish_result)
            if publish_data:
                print(f"  {json.dumps(publish_data, indent=2)}")
            else:
                print(f"  {extract_text(publish_result)}")

            # Step 7: Verify - list servers in environment
            print(f"\n=== Step 7: Listing servers in Dataverse environment ===")
            env_servers = await session.call_tool("ListMCPServersInDataverseEnvironment", {
                "envId": ENVIRONMENT_ID,
            })
            env_data = parse_json_response(env_servers)
            if env_data:
                print(f"  {json.dumps(env_data, indent=2)}")
            else:
                print(f"  {extract_text(env_servers)}")

            print(f"\n{'='*60}")
            print(f"MCP Server '{CUSTOM_SERVER_NAME}' registered in environment {ENVIRONMENT_ID}")
            print(f"Remote MCP URL: {REMOTE_MCP_URL}")
            print(f"\nNext: Run 'a365 develop list-available' to verify the server appears.")
            print(f"Or use 'a365 develop-mcp publish' for tenant-wide publishing.")


if __name__ == "__main__":
    asyncio.run(run())
