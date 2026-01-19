# Backend

FastAPI REST API backend for Azure OpenAI chatbot using EntraID authentication and the Responses API.

## Architecture

The backend is built with **FastAPI** and provides a stateful conversation API that:
- Uses **Azure OpenAI Foundry** with **EntraID (Entra ID)** authentication (keyless)
- Implements the **Azure OpenAI Responses API** for stateful conversations with response chaining
- Manages in-memory chat sessions with full conversation history
- Supports CORS for frontend integration

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/` | API information and endpoint listing |
| `POST` | `/sessions` | Create a new chat session |
| `GET` | `/sessions` | List all active sessions |
| `GET` | `/sessions/{session_id}` | Get session details |
| `POST` | `/sessions/{session_id}/messages` | Send a message and get response |
| `DELETE` | `/sessions/{session_id}` | Delete a session |
| `GET` | `/health` | Health check endpoint |

## How It Works

1. **Session Creation**: Each chat conversation is a separate session with a unique UUID
2. **Message Flow**: Messages are sent to Azure OpenAI Responses API, maintaining conversation context
3. **Response Chaining**: Uses `previous_response_id` to chain responses for efficient context management
4. **Authentication**: Uses `DefaultAzureCredential` for EntraID token-based authentication
5. **Storage**: Sessions are stored in-memory (not persistent across restarts)

## Configuration

Required environment variables:
- `AZURE_OPENAI_ENDPOINT` - Azure OpenAI endpoint URL
- `AZURE_OPENAI_MODEL_DEPLOYMENT` - Model deployment name (default: gpt-4o)
- `API_HOST` - Server host (default: 0.0.0.0)
- `API_PORT` - Server port (default: 8000)
