import urllib.parse
from typing import Dict, Any, Optional, List
import httpx
from config import settings

GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v2/userinfo"

SCOPES_MAP = {
    "google-analytics": [
        "https://www.googleapis.com/auth/analytics.readonly",
        "openid",
        "email",
        "profile"
    ],
    "search-console": [
        "https://www.googleapis.com/auth/webmasters.readonly",
        "openid",
        "email",
        "profile"
    ],
    "all": [
        "https://www.googleapis.com/auth/analytics.readonly",
        "https://www.googleapis.com/auth/webmasters.readonly",
        "openid",
        "email",
        "profile"
    ]
}

def get_scopes_for_service(service: str = "all") -> List[str]:
    """Returns required scopes based on service key."""
    return SCOPES_MAP.get(service, SCOPES_MAP["all"])

def build_google_auth_url(
    service: str = "all", 
    state: Optional[str] = None, 
    redirect_uri: Optional[str] = None
) -> str:
    """
    Constructs Google OAuth 2.0 authorization URL with offline access
    and prompt=consent so a refresh token is guaranteed.
    """
    scopes = get_scopes_for_service(service)
    effective_redirect_uri = redirect_uri or settings.GOOGLE_REDIRECT_URI

    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": effective_redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "offline",
        "prompt": "select_account consent",
        "include_granted_scopes": "true",
        "state": state or service
    }

    return f"{GOOGLE_AUTH_ENDPOINT}?{urllib.parse.urlencode(params)}"

async def exchange_google_code(
    code: str, 
    redirect_uri: Optional[str] = None
) -> Dict[str, Any]:
    """
    Exchanges an authorization code for access and refresh tokens.
    """
    effective_redirect_uri = redirect_uri or settings.GOOGLE_REDIRECT_URI

    payload = {
        "code": code,
        "client_id": settings.GOOGLE_CLIENT_ID,
        "client_secret": settings.GOOGLE_CLIENT_SECRET,
        "redirect_uri": effective_redirect_uri,
        "grant_type": "authorization_code"
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(GOOGLE_TOKEN_ENDPOINT, data=payload)
        if resp.status_code != 200:
            error_data = resp.json() if "application/json" in resp.headers.get("content-type", "") else resp.text
            raise RuntimeError(f"Google token exchange failed: {error_data}")
        return resp.json()

async def refresh_google_access_token(refresh_token: str) -> Dict[str, Any]:
    """
    Refreshes an expired access token using the stored refresh token.
    """
    payload = {
        "refresh_token": refresh_token,
        "client_id": settings.GOOGLE_CLIENT_ID,
        "client_secret": settings.GOOGLE_CLIENT_SECRET,
        "grant_type": "refresh_token"
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(GOOGLE_TOKEN_ENDPOINT, data=payload)
        if resp.status_code != 200:
            error_data = resp.json() if "application/json" in resp.headers.get("content-type", "") else resp.text
            raise RuntimeError(f"Failed to refresh Google token: {error_data}")
        return resp.json()

async def fetch_google_user_info(access_token: str) -> Dict[str, Any]:
    """
    Fetches the authenticated Google user's profile info (email, name, picture).
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(GOOGLE_USERINFO_ENDPOINT, headers=headers)
        if resp.status_code != 200:
            return {}
        return resp.json()

async def get_service_account_access_token(
    service_account_info: Dict[str, Any],
    scopes: List[str]
) -> str:
    """
    Generates a Google OAuth access token using a Service Account JSON private key.
    Uses RS256 JWT exchange at Google's OAuth2 token endpoint.
    """
    import time
    import jwt

    client_email = service_account_info.get("client_email")
    private_key = service_account_info.get("private_key")
    token_uri = service_account_info.get("token_uri", GOOGLE_TOKEN_ENDPOINT)

    if not client_email or not private_key:
        raise ValueError("Service account JSON must contain 'client_email' and 'private_key'.")

    now = int(time.time())
    payload = {
        "iss": client_email,
        "sub": client_email,
        "aud": token_uri,
        "scope": " ".join(scopes),
        "iat": now,
        "exp": now + 3600
    }

    signed_jwt = jwt.encode(payload, private_key, algorithm="RS256")

    data = {
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": signed_jwt
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(token_uri, data=data)
        if resp.status_code != 200:
            raise RuntimeError(f"Service account token request failed: {resp.text}")
        token_data = resp.json()
        return token_data.get("access_token")

