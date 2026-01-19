# start_server.py
# Server wrapper based on Microsoft 365 Agents SDK quickstart
import json
import base64
from os import environ
from microsoft_agents.hosting.core import AgentApplication, AgentAuthConfiguration
from microsoft_agents.hosting.aiohttp import (
   start_agent_process,
   jwt_authorization_middleware,
   CloudAdapter,
)
from aiohttp.web import Request, Response, Application, run_app


# Track seen conversations to limit debug output
SEEN_CONVERSATIONS = set()


def start_server(
   agent_application: AgentApplication, auth_configuration: AgentAuthConfiguration
):
   async def entry_point(req: Request) -> Response:
      # Debug: Print decoded JWT token only for new conversations
      try:
         # Peak at body to identify conversation without consuming stream (aiohttp caches read)
         body = await req.json()
         conversation_id = body.get("conversation", {}).get("id")
         
         if conversation_id and conversation_id not in SEEN_CONVERSATIONS:
            SEEN_CONVERSATIONS.add(conversation_id)
            
            auth_header = req.headers.get("Authorization", "")
            if "Bearer " in auth_header:
               try:
                  token = auth_header.split("Bearer ")[1].strip()
                  # JWT has three parts: header.payload.signature
                  token_parts = token.split('.')
                  if len(token_parts) >= 2:
                     # Decode header (first part)
                     header_b64 = token_parts[0]
                     header_b64 += '=' * (-len(header_b64) % 4)
                     header = json.loads(base64.urlsafe_b64decode(header_b64).decode('utf-8'))
                     
                     # Decode payload (second part)
                     payload_b64 = token_parts[1]
                     payload_b64 += '=' * (-len(payload_b64) % 4)
                     payload = json.loads(base64.urlsafe_b64decode(payload_b64).decode('utf-8'))
                     
                     print(f"\n[DEBUG] JWT Token (New Conversation):")
                     print(f"Header:\n{json.dumps(header, indent=2)}")
                     print(f"Payload (All Claims):\n{json.dumps(payload, indent=2, sort_keys=True)}\n")
               except Exception as e:
                  print(f"[DEBUG] Error decoding token: {e}")
      except Exception:
         pass

      agent: AgentApplication = req.app["agent_app"]
      adapter: CloudAdapter = req.app["adapter"]
      return await start_agent_process(
            req,
            agent,
            adapter,
      )

   APP = Application(middlewares=[jwt_authorization_middleware])
   APP.router.add_post("/api/messages", entry_point)
   APP.router.add_get("/api/messages", lambda _: Response(status=200))
   APP["agent_configuration"] = auth_configuration
   APP["agent_app"] = agent_application
   APP["adapter"] = agent_application.adapter

   port = int(environ.get("PORT", 3978))
   print(f"Teams Agent starting on http://localhost:{port}/api/messages")
   
   try:
      run_app(APP, host="0.0.0.0", port=port)
   except Exception as error:
      raise error
