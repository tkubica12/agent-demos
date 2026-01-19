# app.py
"""Simple agent using Microsoft 365 Agent SDK with streaming responses, SSO, and OBO to custom API."""
import asyncio
import os
import re
import aiohttp
from dotenv import load_dotenv
from microsoft_agents.hosting.core import (
    AgentApplication,
    TurnState,
    TurnContext,
    MemoryStorage,
    Authorization,
)
from microsoft_agents.hosting.aiohttp import CloudAdapter, Citation
from microsoft_agents.authentication.msal import MsalConnectionManager
from microsoft_agents.activity import load_configuration_from_env, Activity, Attachment
from start_server import start_server

# Load environment variables from .env file
load_dotenv()

# Load SDK configuration from environment variables
agents_sdk_config = load_configuration_from_env(dict(os.environ))

# Get Empty API configuration from environment
EMPTY_API_URL = os.getenv("EMPTY_API_URL", "http://localhost:8000")
EMPTY_API_SCOPE = os.getenv("EMPTY_API_SCOPE", "")

# Create storage, connection manager, and authorization for SSO
STORAGE = MemoryStorage()
CONNECTION_MANAGER = MsalConnectionManager(**agents_sdk_config)
ADAPTER = CloudAdapter(connection_manager=CONNECTION_MANAGER)
AUTHORIZATION = Authorization(STORAGE, CONNECTION_MANAGER, **agents_sdk_config)


async def get_graph_user_profile(access_token: str) -> dict:
    """
    Fetch user profile from Microsoft Graph using the provided access token.
    The token should already be an access token for Graph API (from OBO exchange).
    """
    try:
        async with aiohttp.ClientSession() as session:
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json"
            }
            async with session.get("https://graph.microsoft.com/v1.0/me", headers=headers) as response:
                if response.status == 200:
                    user = await response.json()
                    return {
                        "success": True,
                        "display_name": user.get("displayName"),
                        "mail": user.get("mail") or user.get("userPrincipalName"),
                        "job_title": user.get("jobTitle"),
                        "id": user.get("id")
                    }
                else:
                    error_text = await response.text()
                    return {
                        "success": False,
                        "error": f"Graph API error {response.status}: {error_text}"
                    }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


async def call_empty_api(access_token: str) -> dict:
    """
    Call the Empty API using the provided access token (obtained via OBO flow).
    Returns the API response including debug information about the JWT token.
    """
    try:
        async with aiohttp.ClientSession() as session:
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json"
            }
            api_url = f"{EMPTY_API_URL}/emptydata"
            print(f"[Empty API] Calling {api_url}")
            
            async with session.get(api_url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    print(f"[Empty API] Success: {data.get('message', 'No message')}")
                    return {
                        "success": True,
                        "data": data
                    }
                else:
                    error_text = await response.text()
                    print(f"[Empty API] Error {response.status}: {error_text}")
                    return {
                        "success": False,
                        "error": f"Empty API error {response.status}: {error_text}"
                    }
    except Exception as e:
        print(f"[Empty API] Exception: {e}")
        return {
            "success": False,
            "error": str(e)
        }


# Create the Agent Application with Authorization for SSO
AGENT_APP = AgentApplication[TurnState](
    storage=STORAGE,
    adapter=ADAPTER,
    authorization=AUTHORIZATION,
    **agents_sdk_config
)


@AGENT_APP.activity("invoke")
async def handle_invoke(context: TurnContext, state: TurnState):
    """Handle invoke activities (non-SSO)."""
    if context.activity.name != "signin/tokenExchange":
        await context.send_activity(
            Activity(
                type="invokeResponse",
                value={"status": 200}
            )
        )


async def _help(context: TurnContext, _: TurnState):
    """Handle help command and welcome messages."""
    await context.send_activity(
        "Welcome to the Empty Demo Agent! 🚀\n\n"
        "This agent demonstrates:\n"
        "- **SSO** with Teams\n"
        "- **OBO flow** to Microsoft Graph API\n"
        "- **OBO flow** to a custom Empty API\n\n"
        "Send me any message to see it in action!"
    )


# Register handlers for different events
AGENT_APP.conversation_update("membersAdded")(_help)
AGENT_APP.message("/help")(_help)


@AGENT_APP.message("/signout")
async def signout(context: TurnContext, state: TurnState):
    """Sign out the user from all OAuth connections."""
    if AGENT_APP.auth:
        await AGENT_APP.auth.sign_out(context, state)
        await context.send_activity("You have been signed out. 👋")
    else:
        await context.send_activity("Authorization is not configured.")


@AGENT_APP.on_sign_in_success
async def handle_sign_in_success(context: TurnContext, state: TurnState, handler_id: str = None):
    """Called when user successfully signs in via OAuth."""
    await context.send_activity(
        f"✅ Successfully signed in to {handler_id or 'service'}! You can now use authenticated features."
    )


# Handle all other messages with SSO authentication
# Using both GRAPH and EMPTYAPI handlers for OBO to respective APIs
@AGENT_APP.message(re.compile(r".*"), auth_handlers=["GRAPH", "EMPTYAPI"])
async def on_message(context: TurnContext, state: TurnState):
    """Respond to any message with SSO user info, Empty API data, and streaming response."""
    user_message = context.activity.text or "(empty message)"
    
    # ========================================================================
    # SSO: Get Graph API token via OBO flow
    # ========================================================================
    graph_user_info = None
    graph_sso_status = "Not attempted"
    
    try:
        if AGENT_APP.auth:
            token_response = await AGENT_APP.auth.exchange_token(
                context,
                scopes=["User.Read"],
                auth_handler_id="GRAPH"
            )
            
            if token_response and token_response.token:
                graph_sso_status = "Token acquired ✅"
                print(f"[SSO] Got Graph token via exchange_token()")
                graph_user_info = await get_graph_user_profile(token_response.token)
                
                if graph_user_info["success"]:
                    print(f"[SSO] Graph API call successful: {graph_user_info['display_name']}")
                else:
                    print(f"[SSO] Graph API call failed: {graph_user_info['error']}")
            else:
                graph_sso_status = "No token returned from exchange ⚠️"
                print("[SSO] No Graph token returned from exchange_token()")
        else:
            graph_sso_status = "Authorization not configured ❌"
            print("[SSO] AGENT_APP.auth is not configured")
            
    except Exception as e:
        graph_sso_status = f"Error: {str(e)[:50]}..."
        print(f"[SSO] Error during Graph token exchange: {e}")
    
    # ========================================================================
    # Get token for Empty API using the EMPTYAPI auth handler (Non-OBO pattern)
    # The EMPTYAPI handler is connected to Azure Bot OAuth "emptyapi" connection
    # which already returns a token with Empty API as audience - use directly!
    # ========================================================================
    empty_api_token = None
    try:
        if AGENT_APP.auth:
            # Use get_token() NOT exchange_token() - the Azure Bot OAuth connection
            # already returns a token FOR the Empty API (with Empty API as audience)
            # No OBO exchange needed - use the token directly
            empty_api_response = await AGENT_APP.auth.get_token(context, "EMPTYAPI")
            if empty_api_response and empty_api_response.token:
                empty_api_token = empty_api_response.token
                print(f"[Auth] Got Empty API token via get_token() (non-OBO)")
    except Exception as e:
        print(f"[Auth] Error getting Empty API token: {e}")
    
    # ========================================================================
    # Call Empty API with the token we obtained
    # ========================================================================
    empty_api_result = None
    empty_api_status = "Not attempted"
    
    if empty_api_token:
        try:
            empty_api_status = "Token acquired (non-OBO) ✅"
            print(f"[Auth] Using Empty API token from get_token()")
            
            # Call the Empty API with the token
            empty_api_result = await call_empty_api(empty_api_token)
            
            if empty_api_result["success"]:
                empty_api_status = "API call successful ✅"
                print(f"[Auth] Empty API call successful")
            else:
                empty_api_status = f"API call failed: {empty_api_result['error'][:50]}..."
                print(f"[Auth] Empty API call failed: {empty_api_result['error']}")
                
        except Exception as e:
            empty_api_status = f"Error: {str(e)[:50]}..."
            print(f"[Auth] Error calling Empty API: {e}")
    else:
        empty_api_status = "No token available for Empty API ⚠️"
        print("[Auth] No token available for Empty API call")
    
    # ========================================================================
    # Build Debug Information for Adaptive Card
    # ========================================================================
    debug_facts = []
    
    # Graph API facts
    debug_facts.append({"title": "Graph SSO Status", "value": graph_sso_status})
    if graph_user_info and graph_user_info["success"]:
        debug_facts.append({"title": "Graph User", "value": graph_user_info['display_name']})
        debug_facts.append({"title": "Graph Email", "value": graph_user_info['mail'] or "N/A"})
    
    # Empty API facts
    debug_facts.append({"title": "Empty API Status", "value": empty_api_status})
    if empty_api_result and empty_api_result["success"]:
        debug_facts.append({"title": "API Message", "value": empty_api_result["data"].get("message", "N/A")})
    
    # Activity context
    debug_facts.append({"title": "From", "value": context.activity.from_property.name if context.activity.from_property else "Unknown"})
    debug_facts.append({"title": "Channel", "value": context.activity.channel_id or "Unknown"})
    debug_facts.append({"title": "Timestamp", "value": context.activity.timestamp.strftime('%Y-%m-%d %H:%M:%S') if context.activity.timestamp else "N/A"})
    
    # Print to terminal
    print("\n" + "=" * 60)
    print("RESPONSE DEBUG INFO")
    print("=" * 60)
    for fact in debug_facts:
        print(f"{fact['title']}: {fact['value']}")
    print("=" * 60 + "\n")
    
    # ========================================================================
    # Stream Response
    # ========================================================================
    context.streaming_response.set_feedback_loop(True)
    context.streaming_response.set_generated_by_ai_label(True)
    
    # Thinking events
    thinking_events = [
        "Got it, looking into it",
        "Fetching your profile from Graph API",
        "Calling Empty API with OBO token",
        "Processing response"
    ]
    
    for event_text in thinking_events:
        context.streaming_response.queue_informative_update(event_text)
        await asyncio.sleep(1.5)
    
    # Build personalized greeting
    if graph_user_info and graph_user_info["success"]:
        greeting = f"Hello **{graph_user_info['display_name']}**! 👋"
        if graph_user_info["job_title"]:
            greeting += f" I see you're a {graph_user_info['job_title']}."
    else:
        greeting = "Hello! 👋 (SSO not available - couldn't fetch your profile)"
    
    # Build Empty API response summary
    if empty_api_result and empty_api_result["success"]:
        api_message = empty_api_result["data"].get("message", "No message")
        api_summary = f"\n\n🌐 **Empty API says:** {api_message}"
    else:
        api_summary = "\n\n🌐 **Empty API:** Could not retrieve data"
    
    # Stream the response
    original_response = f"{greeting}{api_summary} [doc1][doc2]"
    
    words = original_response.split()
    for word in words:
        context.streaming_response.queue_text_chunk(word + " ")
        await asyncio.sleep(0.1)
    
    # Set citations
    context.streaming_response.set_citations([
        Citation(
            title="Microsoft 365 Agent SDK Documentation",
            content="The Microsoft 365 Agent SDK enables building enterprise-grade "
                   "conversational AI agents with features like citations and streaming.",
            filepath="",
            url="https://learn.microsoft.com/en-us/microsoft-365-copilot/extensibility/"
        ),
        Citation(
            title="On-Behalf-Of Flow Documentation",
            content="The OBO flow allows a web API to exchange an incoming token for a new token "
                   "to call downstream APIs on behalf of the user.",
            filepath="",
            url="https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-on-behalf-of-flow"
        )
    ])
    
    # End the stream
    await context.streaming_response.end_stream()
    
    # ========================================================================
    # Send OBO Flow Demo Adaptive Card
    # ========================================================================
    obo_demo_card = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.5",
        "body": [
            {
                "type": "TextBlock",
                "text": "🔐 OBO Flow Demo Adaptive Card",
                "weight": "Bolder",
                "size": "Large",
                "color": "Accent"
            },
            {
                "type": "FactSet",
                "facts": [
                    {
                        "title": "Graph API",
                        "value": "✅ Success" if (graph_user_info and graph_user_info["success"]) else "❌ Failed"
                    },
                    {
                        "title": "User",
                        "value": graph_user_info["display_name"] if (graph_user_info and graph_user_info["success"]) else "N/A"
                    },
                    {
                        "title": "Empty API",
                        "value": "✅ Success" if (empty_api_result and empty_api_result["success"]) else "❌ Failed"
                    },
                    {
                        "title": "API Message",
                        "value": empty_api_result["data"]["message"] if (empty_api_result and empty_api_result["success"]) else "N/A"
                    }
                ]
            },
            {
                "type": "Container",
                "style": "emphasis",
                "items": [
                    {
                        "type": "TextBlock",
                        "text": "📊 Token Claims from Empty API",
                        "weight": "Bolder"
                    },
                    {
                        "type": "TextBlock",
                        "text": f"User: {empty_api_result['data']['debug']['claims'].get('name', 'N/A')}" if (empty_api_result and empty_api_result["success"] and "debug" in empty_api_result["data"]) else "No claims available",
                        "wrap": True,
                        "size": "Small"
                    },
                    {
                        "type": "TextBlock",
                        "text": f"Scope: {empty_api_result['data']['debug']['claims'].get('scp', 'N/A')}" if (empty_api_result and empty_api_result["success"] and "debug" in empty_api_result["data"]) else "",
                        "wrap": True,
                        "size": "Small"
                    }
                ]
            }
        ],
        "actions": [
            {
                "type": "Action.OpenUrl",
                "title": "📚 OBO Flow Docs",
                "url": "https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-on-behalf-of-flow"
            }
        ]
    }
    
    obo_card_attachment = Attachment(
        content_type="application/vnd.microsoft.card.adaptive",
        content=obo_demo_card
    )
    
    await context.send_activity(Activity(
        type="message",
        attachments=[obo_card_attachment]
    ))
    
    # ========================================================================
    # Send Debug Adaptive Card
    # ========================================================================
    debug_card_body = [
        {
            "type": "TextBlock",
            "text": "🐛 Debug Adaptive Card",
            "weight": "Bolder",
            "size": "Large",
            "color": "Accent"
        },
        {
            "type": "TextBlock",
            "text": f"📨 You wrote: {user_message}",
            "wrap": True
        },
        {
            "type": "FactSet",
            "facts": debug_facts
        }
    ]
    
    # Add JWT claims if available
    if empty_api_result and empty_api_result["success"] and "debug" in empty_api_result["data"]:
        claims = empty_api_result["data"]["debug"].get("claims", {})
        claims_text = []
        for key, value in claims.items():
            str_value = str(value)
            if len(str_value) > 50:
                str_value = str_value[:50] + "..."
            claims_text.append(f"**{key}:** {str_value}")
        
        debug_card_body.append({
            "type": "Container",
            "style": "emphasis",
            "items": [
                {
                    "type": "TextBlock",
                    "text": "🎫 JWT Claims Received by Empty API",
                    "weight": "Bolder"
                },
                {
                    "type": "TextBlock",
                    "text": "\n".join(claims_text) if claims_text else "No claims",
                    "wrap": True,
                    "size": "Small"
                }
            ]
        })
    
    debug_card = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.5",
        "body": debug_card_body
    }
    
    debug_card_attachment = Attachment(
        content_type="application/vnd.microsoft.card.adaptive",
        content=debug_card
    )
    
    await context.send_activity(Activity(
        type="message",
        attachments=[debug_card_attachment]
    ))


# Start the server
if __name__ == "__main__":
    try:
        auth_config = CONNECTION_MANAGER.get_default_connection_configuration()
        
        print("=" * 60)
        print("Empty Demo Agent")
        print("=" * 60)
        print(f"App ID: {auth_config.CLIENT_ID}")
        print(f"Tenant ID: {auth_config.TENANT_ID}")
        print(f"Empty API URL: {EMPTY_API_URL}")
        print(f"Empty API Scope: {EMPTY_API_SCOPE}")
        print("=" * 60)
        
        start_server(AGENT_APP, auth_config)
        
    except Exception as error:
        print(f"Error starting server: {error}")
        raise error
