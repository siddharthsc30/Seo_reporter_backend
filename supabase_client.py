import os
import time
import hashlib
import logging
from typing import Optional, Dict, Tuple
import httpx
from supabase import create_client, Client

try:
    from supabase.lib.client_options import SyncClientOptions
except ImportError:
    from supabase.lib.client_options import ClientOptions as SyncClientOptions  # type: ignore

from config import settings

logger = logging.getLogger(__name__)

# Singletons for unauthenticated anon client and server-side service role client
_client: Optional[Client] = None
_service_client: Optional[Client] = None

# Persistent shared async HTTP client for token verification and external calls
_async_http_client: Optional[httpx.AsyncClient] = None

# In-memory TTL cache for verified token payloads: sha256(token) -> (payload_dict, expires_at)
_TOKEN_CACHE: Dict[str, Tuple[dict, float]] = {}
_TOKEN_CACHE_TTL_SECONDS: float = 60.0


def _prune_token_cache(now: float) -> None:
    """Removes expired entries from the in-memory token cache."""
    expired_keys = [k for k, (_, exp) in _TOKEN_CACHE.items() if exp <= now]
    for k in expired_keys:
        _TOKEN_CACHE.pop(k, None)


def get_async_http_client() -> httpx.AsyncClient:
    """Returns a shared httpx.AsyncClient instance for connection pooling."""
    global _async_http_client
    if _async_http_client is None or _async_http_client.is_closed:
        _async_http_client = httpx.AsyncClient(timeout=10.0)
    return _async_http_client


async def close_supabase_http_client() -> None:
    """Gracefully closes the shared HTTP client connection pool on app shutdown."""
    global _async_http_client
    if _async_http_client is not None and not _async_http_client.is_closed:
        await _async_http_client.aclose()
        _async_http_client = None


def get_supabase_client() -> Client:
    """
    Returns the singleton unauthenticated/anon Supabase client.
    Used for public operations (e.g., viewing public shared reports) subject to public RLS policies.
    """
    global _client
    if not settings.SUPABASE_URL or not settings.SUPABASE_KEY:
        raise ValueError("Supabase configuration error: SUPABASE_URL and SUPABASE_KEY must be set.")

    if _client is None:
        _client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
    return _client


def get_authenticated_client(access_token: str) -> Client:
    """
    Returns a Supabase client scoped to the authenticated user's JWT.
    Enforces user context for Row-Level Security (RLS) across both PostgREST and Storage.
    """
    if not access_token or not access_token.strip():
        raise ValueError("access_token is required to create an authenticated Supabase client.")

    clean_token = access_token.strip()
    if not settings.SUPABASE_URL or not settings.SUPABASE_KEY:
        raise ValueError("Supabase configuration error: SUPABASE_URL and SUPABASE_KEY must be set.")

    options = SyncClientOptions(headers={"Authorization": f"Bearer {clean_token}"})
    client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY, options=options)
    client.postgrest.auth(clean_token)
    return client


def get_service_role_client() -> Client:
    """
    Returns a Supabase client configured with the service-role key.
    WARNING: Bypasses Row-Level Security (RLS). Use strictly for server-side administrative/background tasks.
    Never expose this client or service role key to the frontend.
    """
    global _service_client
    service_key = getattr(settings, "SUPABASE_SERVICE_ROLE_KEY", "") or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    if not service_key:
        raise ValueError("SUPABASE_SERVICE_ROLE_KEY is not configured in settings or environment.")

    if not settings.SUPABASE_URL:
        raise ValueError("SUPABASE_URL must be set.")

    if _service_client is None:
        _service_client = create_client(settings.SUPABASE_URL, service_key)
    return _service_client


async def verify_supabase_token(access_token: str) -> Optional[dict]:
    """
    Validates a Supabase JWT against the Supabase Auth endpoint /auth/v1/user.
    Uses connection pooling via a shared httpx.AsyncClient and an in-memory TTL cache
    to avoid redundant network round-trips and prevent rate-limiting.
    Returns user payload dictionary if valid, None otherwise.
    """
    if not access_token or not access_token.strip():
        return None

    clean_token = access_token.strip()
    now = time.time()

    # Check cache first using hashed token key (avoids storing raw tokens in memory keys)
    token_hash = hashlib.sha256(clean_token.encode("utf-8")).hexdigest()
    cached = _TOKEN_CACHE.get(token_hash)
    if cached:
        payload, exp_time = cached
        if exp_time > now:
            return payload
        else:
            _TOKEN_CACHE.pop(token_hash, None)

    if not settings.SUPABASE_URL or not settings.SUPABASE_KEY:
        logger.error("Supabase configuration missing: SUPABASE_URL or SUPABASE_KEY.")
        return None

    url = f"{settings.SUPABASE_URL}/auth/v1/user"
    headers = {
        "apikey": settings.SUPABASE_KEY,
        "Authorization": f"Bearer {clean_token}"
    }

    try:
        http_client = get_async_http_client()
        resp = await http_client.get(url, headers=headers)
        if resp.status_code == 200:
            payload = resp.json()
            _prune_token_cache(now)
            _TOKEN_CACHE[token_hash] = (payload, now + _TOKEN_CACHE_TTL_SECONDS)
            return payload
        elif resp.status_code == 401:
            logger.warning("Supabase token verification failed: Invalid or expired token (HTTP 401).")
        else:
            logger.warning("Supabase auth endpoint returned unexpected HTTP status: %d", resp.status_code)
        return None
    except httpx.RequestError as exc:
        logger.error("Network error while connecting to Supabase auth endpoint: %s", str(exc))
        return None
    except Exception as exc:
        logger.exception("Unexpected error verifying Supabase token: %s", str(exc))
        return None
