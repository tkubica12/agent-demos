"""
Backend API for Azure OpenAI Chatbot.

This module implements a FastAPI backend that provides a proprietary API
for chatting with Azure OpenAI in Foundry using EntraID authentication
and the Responses API.
"""

import os
import uuid
from typing import Dict, List, Optional
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from openai import OpenAI
from azure.identity import DefaultAzureCredential, get_bearer_token_provider

# Load environment variables
load_dotenv()

# Initialize FastAPI app
app = FastAPI(
    title="Azure OpenAI Chat API",
    description="Proprietary API for chatting with Azure OpenAI using Responses API",
    version="1.0.0",
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Azure OpenAI client with EntraID authentication
token_provider = get_bearer_token_provider(
    DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
)

client = OpenAI(
    base_url=os.getenv("AZURE_OPENAI_ENDPOINT") + "/openai/v1/",
    api_key=token_provider,
)

# In-memory storage for chat sessions
chat_sessions: Dict[str, Dict] = {}


# Pydantic models
class ChatSession(BaseModel):
    """Chat session response model."""

    session_id: str = Field(..., description="Unique session identifier")
    created_at: str = Field(..., description="Session creation timestamp")
    model: str = Field(..., description="Azure OpenAI model deployment name")


class MessageRequest(BaseModel):
    """Message request model."""

    message: str = Field(..., description="User message content")


class MessageResponse(BaseModel):
    """Message response model."""

    session_id: str = Field(..., description="Session identifier")
    user_message: str = Field(..., description="User's message")
    assistant_message: str = Field(..., description="Assistant's response")
    response_id: str = Field(..., description="OpenAI response ID")
    timestamp: str = Field(..., description="Response timestamp")


class SessionDetail(BaseModel):
    """Detailed session information model."""

    session_id: str
    created_at: str
    model: str
    message_count: int
    last_response_id: Optional[str] = None


# API endpoints
@app.get("/")
async def root():
    """Root endpoint with API information."""
    return {
        "name": "Azure OpenAI Chat API",
        "version": "1.0.0",
        "description": "Chat with Azure OpenAI using Responses API",
        "endpoints": {
            "create_session": "POST /sessions",
            "get_session": "GET /sessions/{session_id}",
            "list_sessions": "GET /sessions",
            "send_message": "POST /sessions/{session_id}/messages",
            "delete_session": "DELETE /sessions/{session_id}",
        },
    }


@app.post("/sessions", response_model=ChatSession)
async def create_session():
    """
    Create a new chat session.

    Returns:
        ChatSession: New session information including session_id
    """
    session_id = str(uuid.uuid4())
    model = os.getenv("AZURE_OPENAI_MODEL_DEPLOYMENT", "gpt-4o")

    chat_sessions[session_id] = {
        "session_id": session_id,
        "created_at": datetime.now().isoformat(),
        "model": model,
        "messages": [],
        "last_response_id": None,
    }

    return ChatSession(
        session_id=session_id,
        created_at=chat_sessions[session_id]["created_at"],
        model=model,
    )


@app.get("/sessions", response_model=List[SessionDetail])
async def list_sessions():
    """
    List all active chat sessions.

    Returns:
        List[SessionDetail]: List of all sessions with their details
    """
    return [
        SessionDetail(
            session_id=session["session_id"],
            created_at=session["created_at"],
            model=session["model"],
            message_count=len(session["messages"]),
            last_response_id=session.get("last_response_id"),
        )
        for session in chat_sessions.values()
    ]


@app.get("/sessions/{session_id}", response_model=SessionDetail)
async def get_session(session_id: str):
    """
    Get details about a specific session.

    Args:
        session_id: The session identifier

    Returns:
        SessionDetail: Session information

    Raises:
        HTTPException: If session not found
    """
    if session_id not in chat_sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    session = chat_sessions[session_id]
    return SessionDetail(
        session_id=session["session_id"],
        created_at=session["created_at"],
        model=session["model"],
        message_count=len(session["messages"]),
        last_response_id=session.get("last_response_id"),
    )


@app.post("/sessions/{session_id}/messages", response_model=MessageResponse)
async def send_message(session_id: str, request: MessageRequest):
    """
    Send a message in a chat session and get a response.

    Args:
        session_id: The session identifier
        request: Message request containing the user's message

    Returns:
        MessageResponse: The assistant's response

    Raises:
        HTTPException: If session not found or API error occurs
    """
    if session_id not in chat_sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    session = chat_sessions[session_id]
    user_message = request.message

    # Add user message to session history
    session["messages"].append({"role": "user", "content": user_message})

    try:
        # Prepare input for Responses API
        input_messages = [{"role": msg["role"], "content": msg["content"]} 
                         for msg in session["messages"]]

        # Call Azure OpenAI Responses API
        # If we have a previous response, chain them together
        if session.get("last_response_id"):
            response = client.responses.create(
                model=session["model"],
                previous_response_id=session["last_response_id"],
                input=[{"role": "user", "content": user_message}],
                reasoning={"effort": "minimal"},
            )
        else:
            # First message in the session
            response = client.responses.create(
                model=session["model"],
                input=input_messages,
                reasoning={"effort": "minimal"},
            )

        # Extract assistant's response
        assistant_message = response.output_text

        # Store response information
        session["messages"].append({"role": "assistant", "content": assistant_message})
        session["last_response_id"] = response.id

        return MessageResponse(
            session_id=session_id,
            user_message=user_message,
            assistant_message=assistant_message,
            response_id=response.id,
            timestamp=datetime.now().isoformat(),
        )

    except Exception as e:
        import traceback
        print(f"Error in send_message: {e}")
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Error communicating with Azure OpenAI: {str(e)}",
        )


@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """
    Delete a chat session.

    Args:
        session_id: The session identifier

    Returns:
        dict: Confirmation message

    Raises:
        HTTPException: If session not found
    """
    if session_id not in chat_sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    del chat_sessions[session_id]
    return {"message": f"Session {session_id} deleted successfully"}


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "azure_openai_configured": bool(os.getenv("AZURE_OPENAI_ENDPOINT"))}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.getenv("API_HOST", "0.0.0.0"),
        port=int(os.getenv("API_PORT", "8000")),
    )
