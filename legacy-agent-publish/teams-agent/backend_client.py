import aiohttp
from typing import Optional, Dict, Any

class BackendClient:
    """Client for communicating with the proprietary backend API."""
    
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip('/')

    async def create_session(self) -> str:
        """Creates a new session and returns the session_id."""
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{self.base_url}/sessions") as response:
                if response.status == 200:
                    data = await response.json()
                    return data["session_id"]
                else:
                    raise Exception(f"Failed to create session: {response.status}")

    async def send_message(self, session_id: str, message: str) -> str:
        """Sends a message to a session and returns the assistant's response."""
        async with aiohttp.ClientSession() as session:
            payload = {"message": message}
            url = f"{self.base_url}/sessions/{session_id}/messages"
            
            async with session.post(url, json=payload) as response:
                if response.status == 200:
                    data = await response.json()
                    return data["assistant_message"]
                elif response.status == 404:
                    # Session missing/expired on backend
                    raise ValueError("Session not found")
                else:
                    raise Exception(f"Failed to send message: {response.status}")
