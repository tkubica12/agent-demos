# Legacy Agent Publish Demo

This demo projects a proprietary AI chatbot into Microsoft Teams without modifying the legacy code.

## Components

- Backend: proprietary RESTful API that hosts the chatbot logic.
- Frontend: Streamlit UI for direct testing against the backend.
- Teams agent: translation service that uses Microsoft 365 Agents SDK via Azure Bot Service to communicate with Teams and translate messages into REST calls to the backend.

## Folders

- [backend/](backend/)
- [frontend/](frontend/)
- [teams-agent/](teams-agent/)
