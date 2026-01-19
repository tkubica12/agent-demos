# Empty API

A simple FastAPI REST API protected by Microsoft Entra ID. This API is called by the Empty Agent using the On-Behalf-Of (OBO) flow.

## Endpoint

| Method | Path | Description |
|--------|------|-------------|
| GET | `/emptydata` | Returns fixed data + JWT token debug information |
| GET | `/health` | Health check endpoint (no auth required) |

## Response Format

```json
{
  "message": "Here are data from empty api",
  "debug": {
    "token_received": true,
    "claims": {
      "aud": "api://...",
      "iss": "https://login.microsoftonline.com/.../v2.0",
      "name": "John Doe",
      "oid": "...",
      "preferred_username": "john@contoso.com",
      "scp": "access_as_user",
      "tid": "...",
      ...
    }
  }
}
```

## Running Locally

```powershell
# Install dependencies and run
uv run uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

## Configuration

Create a `.env` file based on `.env.example`:

```env
API_CLIENT_ID=<your-api-app-id>
API_TENANT_ID=<your-tenant-id>
API_AUDIENCE=api://<your-api-app-id>
```

## Token Validation

The API validates incoming JWT tokens by:

1. Fetching JWKS (JSON Web Key Set) from Microsoft's OpenID configuration
2. Verifying the token signature using RS256
3. Validating claims: audience, issuer, expiration
4. Extracting and returning claims for debugging
