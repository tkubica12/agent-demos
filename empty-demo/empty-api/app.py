# app.py
"""Empty API - A simple FastAPI protected by Microsoft Entra ID."""
import os
from typing import Optional
from contextlib import asynccontextmanager

import httpx
import jwt
from jwt import PyJWKClient
from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

# Load environment variables
load_dotenv()

# Configuration from environment
API_CLIENT_ID = os.getenv("API_CLIENT_ID", "")
API_TENANT_ID = os.getenv("API_TENANT_ID", "")
API_AUDIENCE = os.getenv("API_AUDIENCE", f"api://{API_CLIENT_ID}")

# Microsoft Entra ID OpenID configuration URLs
OPENID_CONFIG_URL = f"https://login.microsoftonline.com/{API_TENANT_ID}/v2.0/.well-known/openid-configuration"
JWKS_URI: Optional[str] = None
JWKS_CLIENT: Optional[PyJWKClient] = None

# Security scheme
security = HTTPBearer()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize JWKS client on startup."""
    global JWKS_URI, JWKS_CLIENT
    
    print("=" * 60)
    print("Empty API Starting")
    print("=" * 60)
    print(f"API Client ID: {API_CLIENT_ID}")
    print(f"Tenant ID: {API_TENANT_ID}")
    print(f"Expected Audience: {API_AUDIENCE}")
    print("=" * 60)
    
    # Fetch OpenID configuration to get JWKS URI
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(OPENID_CONFIG_URL)
            if response.status_code == 200:
                config = response.json()
                JWKS_URI = config.get("jwks_uri")
                if JWKS_URI:
                    JWKS_CLIENT = PyJWKClient(JWKS_URI)
                    print(f"JWKS URI: {JWKS_URI}")
                else:
                    print("WARNING: Could not find jwks_uri in OpenID configuration")
            else:
                print(f"WARNING: Could not fetch OpenID configuration: {response.status_code}")
    except Exception as e:
        print(f"WARNING: Error fetching OpenID configuration: {e}")
    
    print("=" * 60)
    print("Listening on http://localhost:8000")
    print("Endpoints:")
    print("  GET /health     - Health check (no auth)")
    print("  GET /emptydata  - Protected endpoint (requires Bearer token)")
    print("=" * 60)
    
    yield
    
    # Cleanup (if needed)
    print("Empty API shutting down...")


app = FastAPI(
    title="Empty API",
    description="A simple API protected by Microsoft Entra ID for OBO flow demo",
    version="1.0.0",
    lifespan=lifespan
)


async def validate_token(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """
    Validate the JWT token from the Authorization header.
    Returns the decoded token claims if valid.
    """
    token = credentials.credentials
    
    if not JWKS_CLIENT:
        raise HTTPException(
            status_code=503,
            detail="JWKS client not initialized. Check API configuration."
        )
    
    try:
        # Get the signing key from JWKS
        signing_key = JWKS_CLIENT.get_signing_key_from_jwt(token)
        
        # Azure AD tokens can have different issuer formats depending on token version:
        # v2.0: https://login.microsoftonline.com/{tenant-id}/v2.0
        # v1.0: https://sts.windows.net/{tenant-id}/
        # We accept both formats
        valid_issuers = [
            f"https://login.microsoftonline.com/{API_TENANT_ID}/v2.0",
            f"https://sts.windows.net/{API_TENANT_ID}/",
        ]
        
        # First decode without issuer validation to check what issuer we received
        unverified = jwt.decode(token, options={"verify_signature": False})
        actual_issuer = unverified.get("iss", "")
        
        if actual_issuer not in valid_issuers:
            print(f"[Token Error] Invalid issuer: {actual_issuer}")
            print(f"[Token Error] Expected one of: {valid_issuers}")
            raise HTTPException(
                status_code=401,
                detail=f"Invalid token issuer. Got: {actual_issuer}"
            )
        
        # Decode and validate the token with the actual issuer
        decoded = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=API_AUDIENCE,
            issuer=actual_issuer,
            options={
                "verify_exp": True,
                "verify_aud": True,
                "verify_iss": True,
            }
        )
        
        print(f"[Token Validated] User: {decoded.get('name', 'Unknown')} ({decoded.get('preferred_username', 'N/A')})")
        
        return {
            "token": token,
            "claims": decoded
        }
        
    except jwt.ExpiredSignatureError:
        print("[Token Error] Token has expired")
        raise HTTPException(
            status_code=401,
            detail="Token has expired"
        )
    except jwt.InvalidAudienceError:
        print(f"[Token Error] Invalid audience. Expected: {API_AUDIENCE}")
        raise HTTPException(
            status_code=401,
            detail=f"Invalid audience. Expected: {API_AUDIENCE}"
        )
    except jwt.InvalidIssuerError:
        print("[Token Error] Invalid issuer")
        raise HTTPException(
            status_code=401,
            detail="Invalid token issuer"
        )
    except jwt.InvalidTokenError as e:
        print(f"[Token Error] Invalid token: {e}")
        raise HTTPException(
            status_code=401,
            detail=f"Invalid token: {str(e)}"
        )
    except Exception as e:
        print(f"[Token Error] Unexpected error: {e}")
        raise HTTPException(
            status_code=401,
            detail=f"Token validation failed: {str(e)}"
        )


@app.get("/health")
async def health():
    """Health check endpoint - no authentication required."""
    return {"status": "healthy", "service": "empty-api"}


@app.get("/emptydata")
async def get_empty_data(token_info: dict = Depends(validate_token)):
    """
    Protected endpoint that returns fixed data along with JWT debug information.
    Requires a valid Bearer token from Microsoft Entra ID.
    """
    claims = token_info["claims"]
    
    # Extract relevant claims for debug output
    debug_claims = {
        "aud": claims.get("aud"),
        "iss": claims.get("iss"),
        "iat": claims.get("iat"),
        "exp": claims.get("exp"),
        "name": claims.get("name"),
        "oid": claims.get("oid"),
        "preferred_username": claims.get("preferred_username"),
        "scp": claims.get("scp"),
        "tid": claims.get("tid"),
        "azp": claims.get("azp"),
        "azpacr": claims.get("azpacr"),
    }
    
    # Remove None values for cleaner output
    debug_claims = {k: v for k, v in debug_claims.items() if v is not None}
    
    return {
        "message": "Here are data from empty api",
        "debug": {
            "token_received": True,
            "claims": debug_claims,
            "all_claims": claims  # Include all claims for full debugging
        }
    }


@app.get("/")
async def root():
    """Root endpoint with API info."""
    return {
        "name": "Empty API",
        "description": "A simple API protected by Microsoft Entra ID",
        "endpoints": {
            "/health": "Health check (no auth)",
            "/emptydata": "Protected endpoint (requires Bearer token)"
        }
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
