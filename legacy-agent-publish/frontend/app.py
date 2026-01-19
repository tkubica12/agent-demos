"""
Streamlit frontend for Azure OpenAI Chatbot.

This module implements a chat interface using Streamlit that communicates
with the FastAPI backend.
"""

import os
import requests
from datetime import datetime
from typing import Optional

import streamlit as st
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")

# Page config
st.set_page_config(
    page_title="Azure OpenAI Chat",
    page_icon="💬",
    layout="wide",
)


# Helper functions
def create_session() -> Optional[str]:
    """Create a new chat session via API."""
    try:
        response = requests.post(f"{API_BASE_URL}/sessions", timeout=10)
        response.raise_for_status()
        data = response.json()
        return data["session_id"]
    except Exception as e:
        st.error(f"Error creating session: {str(e)}")
        return None


def send_message(session_id: str, message: str) -> Optional[dict]:
    """Send a message to the chat API."""
    try:
        response = requests.post(
            f"{API_BASE_URL}/sessions/{session_id}/messages",
            json={"message": message},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        st.error(f"Error sending message: {str(e)}")
        return None


def get_session_info(session_id: str) -> Optional[dict]:
    """Get session information from API."""
    try:
        response = requests.get(f"{API_BASE_URL}/sessions/{session_id}", timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        st.error(f"Error getting session info: {str(e)}")
        return None


def delete_session(session_id: str) -> bool:
    """Delete a session via API."""
    try:
        response = requests.delete(f"{API_BASE_URL}/sessions/{session_id}", timeout=10)
        response.raise_for_status()
        return True
    except Exception as e:
        st.error(f"Error deleting session: {str(e)}")
        return False


# Initialize session state
if "session_id" not in st.session_state:
    st.session_state.session_id = None
if "messages" not in st.session_state:
    st.session_state.messages = []


# Main UI
st.title("💬 Azure OpenAI Chat")
st.caption("Chat with Azure OpenAI using Foundry with EntraID Authentication")

# Sidebar
with st.sidebar:
    st.header("Session Management")

    # Session status
    if st.session_state.session_id:
        st.success(f"**Active Session**")
        st.code(st.session_state.session_id[:8] + "...")

        # Get and display session info
        session_info = get_session_info(st.session_state.session_id)
        if session_info:
            st.info(f"**Model:** {session_info['model']}")
            st.info(f"**Messages:** {session_info['message_count']}")
            created_time = datetime.fromisoformat(session_info["created_at"])
            st.info(f"**Created:** {created_time.strftime('%H:%M:%S')}")

        # New session button
        if st.button("🔄 New Session", use_container_width=True):
            # Optionally delete old session
            if st.session_state.session_id:
                delete_session(st.session_state.session_id)

            st.session_state.session_id = None
            st.session_state.messages = []
            st.rerun()

        # Clear chat button
        if st.button("🗑️ Clear Chat", use_container_width=True):
            st.session_state.messages = []
            st.rerun()

    else:
        st.warning("No active session")
        if st.button("➕ Start New Session", use_container_width=True):
            session_id = create_session()
            if session_id:
                st.session_state.session_id = session_id
                st.session_state.messages = []
                st.rerun()

    st.divider()

    # API Configuration
    st.subheader("Configuration")
    st.text_input(
        "API URL",
        value=API_BASE_URL,
        disabled=True,
        help="Backend API endpoint",
    )

    # Health check
    try:
        health_response = requests.get(f"{API_BASE_URL}/health", timeout=5)
        if health_response.status_code == 200:
            data = health_response.json()
            if data.get("azure_openai_configured"):
                st.success("✅ API Connected")
            else:
                st.warning("⚠️ Azure OpenAI not configured")
        else:
            st.error("❌ API Unavailable")
    except:
        st.error("❌ Cannot reach API")

    st.divider()
    st.caption("Built with FastAPI, Streamlit, and Azure OpenAI")


# Main chat interface
if st.session_state.session_id:
    # Display chat messages
    chat_container = st.container()
    with chat_container:
        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

    # Chat input
    if prompt := st.chat_input("Type your message here..."):
        # Add user message to UI
        st.session_state.messages.append({"role": "user", "content": prompt})

        # Display user message
        with st.chat_message("user"):
            st.markdown(prompt)

        # Get assistant response
        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                response_data = send_message(st.session_state.session_id, prompt)

                if response_data:
                    assistant_message = response_data["assistant_message"]
                    st.markdown(assistant_message)

                    # Add assistant message to history
                    st.session_state.messages.append(
                        {"role": "assistant", "content": assistant_message}
                    )

                    # Show response metadata in expander
                    with st.expander("Response Details"):
                        st.json(
                            {
                                "response_id": response_data["response_id"],
                                "timestamp": response_data["timestamp"],
                            }
                        )
                else:
                    st.error("Failed to get response from API")

else:
    # No active session - show welcome screen
    st.info("👈 Click **'Start New Session'** in the sidebar to begin chatting")

    # Show some example prompts
    st.subheader("Example Prompts")
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("""
        **General Questions**
        - What is Azure OpenAI?
        - Explain machine learning
        - How does cloud computing work?
        """)

    with col2:
        st.markdown("""
        **Technical Help**
        - Write a Python function
        - Explain REST APIs
        - What is containerization?
        """)

    st.divider()

    st.subheader("Features")
    st.markdown("""
    - ✅ **Session Management**: Each chat maintains separate context
    - ✅ **EntraID Authentication**: Secure keyless authentication
    - ✅ **Responses API**: Latest Azure OpenAI stateful API
    - ✅ **Response Chaining**: Conversations maintain full context
    - ✅ **Real-time Updates**: Instant responses from Azure OpenAI
    """)
