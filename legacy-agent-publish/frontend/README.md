# Frontend

Streamlit web interface for the Azure OpenAI chatbot application.

## Architecture

The frontend is built with **Streamlit** and provides an interactive chat UI that:
- Communicates with the FastAPI backend via REST API
- Manages chat sessions with session state
- Displays conversation history in a chat-like interface
- Provides session management and monitoring capabilities

## Features

- **Session Management**: Create new sessions, view session info, clear chat history
- **Chat Interface**: User-friendly chat UI with message history
- **Real-time Updates**: Instant responses from backend API
- **Health Monitoring**: Shows API connection status and configuration
- **Response Metadata**: View response IDs and timestamps for debugging

## How It Works

1. **Initialization**: User creates a session via the sidebar
2. **Chat Flow**:
   - User enters a message in the chat input
   - Frontend sends message to backend API (`POST /sessions/{session_id}/messages`)
   - Backend communicates with Azure OpenAI and returns response
   - Frontend displays the assistant's response in the chat
3. **State Management**: Uses Streamlit session state to maintain:
   - Active session ID
   - Message history for UI display
4. **Session Control**: Users can create new sessions, clear chat, or view session details

## API Integration

The frontend calls these backend endpoints:
- `POST /sessions` - Create new session
- `POST /sessions/{session_id}/messages` - Send messages
- `GET /sessions/{session_id}` - Get session info
- `DELETE /sessions/{session_id}` - Delete session
- `GET /health` - Check API health

## Configuration

Required environment variables:
- `API_BASE_URL` - Backend API URL (default: http://localhost:8000)
