import os
import json
import uuid
import re
from datetime import datetime
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from auth import get_current_user, get_optional_user, UserContext
from supabase_client import get_authenticated_client
from config import settings
from services.google_oauth import refresh_google_access_token
from services.gsc_service import fetch_gsc_sites, fetch_gsc_full_dataset
from services.ga4_service import fetch_ga4_properties, fetch_ga4_full_dataset
from routers.integrations import TOKEN_CACHE

router = APIRouter(prefix="/api/clients", tags=["Clients"])

LOCAL_STORAGE_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "clients_storage.json")

def load_local_clients() -> Dict[str, List[Dict[str, Any]]]:
    """Loads clients grouped by user_id from the local fallback storage file."""
    if os.path.exists(LOCAL_STORAGE_FILE):
        try:
            with open(LOCAL_STORAGE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_local_clients(data: Dict[str, List[Dict[str, Any]]]):
    """Persists clients grouped by user_id to the local fallback storage file."""
    try:
        with open(LOCAL_STORAGE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Error saving local clients storage: {e}")

def sanitize_domain(raw_url: str) -> str:
    """Extracts and normalizes clean domain hostname (e.g., https://www.example.com/ -> example.com)."""
    if not raw_url:
        return ""
    clean = raw_url.strip()
    clean = re.sub(r"^https?:\/\/", "", clean, flags=re.IGNORECASE)
    clean = clean.split("/")[0].split("?")[0].strip()
    return clean.lower()

class ClientCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    website: str = Field(..., min_length=3, max_length=255)
    industry: Optional[str] = None
    logo_url: Optional[str] = None
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    notes: Optional[str] = None
    ga4_property_id: Optional[str] = None
    ga4_property_name: Optional[str] = None
    gsc_site_url: Optional[str] = None
    google_account_email: Optional[str] = None
    google_refresh_token: Optional[str] = None
    semrush_project_id: Optional[str] = None
    is_default: Optional[bool] = False

class ClientUpdateRequest(BaseModel):
    name: Optional[str] = None
    website: Optional[str] = None
    industry: Optional[str] = None
    logo_url: Optional[str] = None
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    notes: Optional[str] = None
    ga4_property_id: Optional[str] = None
    ga4_property_name: Optional[str] = None
    gsc_site_url: Optional[str] = None
    google_account_email: Optional[str] = None
    google_refresh_token: Optional[str] = None
    semrush_project_id: Optional[str] = None
    is_default: Optional[bool] = None

class ClientIntegrationsUpdateRequest(BaseModel):
    ga4_property_id: Optional[str] = None
    ga4_property_name: Optional[str] = None
    gsc_site_url: Optional[str] = None
    google_account_email: Optional[str] = None
    google_refresh_token: Optional[str] = None
    semrush_project_id: Optional[str] = None

class ClientIntegrationDisconnectRequest(BaseModel):
    service: str = "all" # 'google-analytics' | 'search-console' | 'all'

async def resolve_client_google_token(
    user: Optional[UserContext], 
    client_data: Dict[str, Any], 
    service: str = "google-analytics"
) -> Optional[str]:
    """
    Resolves an active Google access token strictly for the requested service:
    1. Dedicated client token from memory cache if unexpired: TOKEN_CACHE[f"{user_id}:{client_id}:{service}"]
    2. Dedicated client refresh token: client_data.get("google_refresh_token")
    3. User-level token from memory cache or DB strictly for this service if client has this service configured.
    """
    import time
    client_id = client_data.get("id")
    user_id = user.id if user else client_data.get("user_id", "default")
    now_ts = time.time()

    # 1. Check client-specific memory cache for this service if still valid
    if client_id:
        cache_key = f"{user_id}:{client_id}:{service}"
        cached_client = TOKEN_CACHE.get(cache_key)
        if cached_client and cached_client.get("accessToken"):
            expires_at = cached_client.get("expires_at", 0)
            if expires_at > now_ts:
                return cached_client["accessToken"]
            else:
                # Expired token in cache; evict it
                TOKEN_CACHE.pop(cache_key, None)

    # 2. Check client's own dedicated refresh token
    client_ref_token = client_data.get("google_refresh_token")
    if client_ref_token:
        try:
            res = await refresh_google_access_token(client_ref_token)
            tok = res.get("access_token")
            if tok:
                expires_in = float(res.get("expires_in", 3599))
                expires_at = now_ts + expires_in - 120  # 2 minute safety buffer
                if client_id:
                    TOKEN_CACHE[f"{user_id}:{client_id}:{service}"] = {
                        "accessToken": tok,
                        "refreshToken": client_ref_token,
                        "expires_at": expires_at
                    }
                return tok
        except Exception as e:
            print(f"Failed to refresh client-specific token: {e}")

    # 3. Fallback to user-level token ONLY IF user is authenticated and client already has configured property
    if user:
        has_configured_prop = bool(
            (service == "google-analytics" and client_data.get("ga4_property_id")) or
            (service == "search-console" and client_data.get("gsc_site_url"))
        )
        if has_configured_prop:
            # Check user's Google tokens in memory cache strictly for this service
            user_cache_key = f"{user.id}:{service}"
            cached_user = TOKEN_CACHE.get(user_cache_key)
            if cached_user and cached_user.get("accessToken"):
                expires_at = cached_user.get("expires_at", 0)
                if expires_at > now_ts:
                    return cached_user["accessToken"]
                else:
                    TOKEN_CACHE.pop(user_cache_key, None)

            # Check DB integrations table strictly for this service
            try:
                supabase = get_authenticated_client(user.access_token)
                res = supabase.table("integrations").select("*").eq("user_id", user.id).eq("name", service).eq("is_connected", True).execute()
                for r in (res.data or []):
                    ref = r.get("refresh_token")
                    if ref:
                        res_tok = await refresh_google_access_token(ref)
                        tok = res_tok.get("access_token")
                        if tok:
                            expires_in = float(res_tok.get("expires_in", 3599))
                            expires_at = now_ts + expires_in - 120
                            TOKEN_CACHE[user_cache_key] = {
                                "accessToken": tok,
                                "refreshToken": ref,
                                "expires_at": expires_at
                            }
                            return tok
            except Exception as e:
                print(f"Error resolving Google token from DB for service {service}: {e}")

    return None


@router.get("", response_model=List[Dict[str, Any]])
async def list_clients(user: Optional[UserContext] = Depends(get_optional_user)):
    """
    Returns all registered clients strictly for the authenticated user.
    Uses Supabase 'clients' table with local JSON fallback scoped only to user.id.
    """
    if not user:
        return []

    store = load_local_clients()

    try:
        client = get_authenticated_client(user.access_token)
        res = client.table("clients").select("*").eq("user_id", user.id).order("created_at", desc=False).execute()
        if res.data:
            return res.data
        elif res.data is not None and len(res.data) == 0:
            # Table exists in Supabase but user has no records yet.
            # Seamlessly sync historical clients from local storage to Supabase
            user_clients = store.get(user.id, [])
            if user_clients:
                for c in user_clients:
                    try:
                        client.table("clients").upsert(c).execute()
                    except Exception as upsert_err:
                        print(f"Notice: Auto-migration client upsert error: {upsert_err}")
                refreshed = client.table("clients").select("*").eq("user_id", user.id).order("created_at", desc=False).execute()
                if refreshed.data:
                    return refreshed.data
    except Exception as e:
        print(f"Notice: Supabase clients query failed (fallback to user local store): {e}")

    # Fallback strictly to local storage for this authenticated user
    return store.get(user.id, [])

@router.post("", status_code=status.HTTP_201_CREATED)
async def create_client(
    payload: ClientCreateRequest,
    user: UserContext = Depends(get_current_user)
):
    """
    Creates a new client record for the authenticated user.
    Sanitizes the domain URL, updates default flags if requested, and persists to Supabase 'clients' table.
    Returns: The newly created client record.
    """
    clean_site = sanitize_domain(payload.website)
    if not clean_site:
        raise HTTPException(status_code=400, detail="Invalid website domain provided.")

    now_iso = datetime.utcnow().isoformat()
    client_id = str(uuid.uuid4())

    new_client = {
        "id": client_id,
        "user_id": user.id,
        "name": payload.name.strip(),
        "website": clean_site,
        "industry": payload.industry.strip() if payload.industry else None,
        "logo_url": payload.logo_url.strip() if payload.logo_url else None,
        "contact_name": payload.contact_name.strip() if payload.contact_name else None,
        "contact_email": payload.contact_email.strip() if payload.contact_email else None,
        "notes": payload.notes.strip() if payload.notes else None,
        "ga4_property_id": payload.ga4_property_id.strip() if payload.ga4_property_id else None,
        "ga4_property_name": payload.ga4_property_name.strip() if payload.ga4_property_name else None,
        "gsc_site_url": payload.gsc_site_url.strip() if payload.gsc_site_url else None,
        "google_account_email": payload.google_account_email.strip() if payload.google_account_email else None,
        "google_refresh_token": payload.google_refresh_token.strip() if payload.google_refresh_token else None,
        "semrush_project_id": payload.semrush_project_id.strip() if payload.semrush_project_id else None,
        "is_default": bool(payload.is_default),
        "created_at": now_iso,
        "updated_at": now_iso
    }

    # Attempt Supabase insert safely
    try:
        client = get_authenticated_client(user.access_token)
        if new_client["is_default"]:
            try:
                client.table("clients").update({"is_default": False}).eq("user_id", user.id).execute()
            except Exception:
                pass

        res = client.table("clients").insert(new_client).execute()
        if res.data and len(res.data) > 0:
            new_client = res.data[0]
    except Exception as e:
        print(f"Notice: Supabase client insert failed (fallback to local): {e}")

    # Fallback / Dual-sync to local storage
    store = load_local_clients()
    user_clients = store.setdefault(user.id, [])
    if new_client["is_default"]:
        for c in user_clients:
            c["is_default"] = False
    user_clients.append(new_client)
    save_local_clients(store)

    return new_client

@router.get("/{client_id}")
async def get_client(
    client_id: str,
    user: UserContext = Depends(get_current_user)
):
    """
    Retrieves a single client by ID strictly for the authenticated user.
    Returns: Client details dictionary or raises 404 if not found.
    """
    try:
        client = get_authenticated_client(user.access_token)
        res = client.table("clients").select("*").eq("id", client_id).eq("user_id", user.id).execute()
        if res.data and len(res.data) > 0:
            return res.data[0]
    except Exception as e:
        print(f"Notice: Supabase client fetch failed (fallback): {e}")

    store = load_local_clients()
    for c in store.get(user.id, []):
        if c.get("id") == client_id:
            return c

    raise HTTPException(status_code=404, detail="Client not found")

@router.put("/{client_id}")
async def update_client(
    client_id: str,
    payload: ClientUpdateRequest,
    user: UserContext = Depends(get_current_user)
):
    """
    Updates client details (name, website, industry, contact, notes, settings) for the authenticated user.
    Returns: The updated client record.
    """
    now_iso = datetime.utcnow().isoformat()
    
    update_data: Dict[str, Any] = {"updated_at": now_iso}
    if payload.name is not None:
        update_data["name"] = payload.name.strip()
    if payload.website is not None:
        update_data["website"] = sanitize_domain(payload.website)
    if payload.industry is not None:
        update_data["industry"] = payload.industry.strip() if payload.industry else None
    if payload.logo_url is not None:
        update_data["logo_url"] = payload.logo_url.strip() if payload.logo_url else None
    if payload.contact_name is not None:
        update_data["contact_name"] = payload.contact_name.strip() if payload.contact_name else None
    if payload.contact_email is not None:
        update_data["contact_email"] = payload.contact_email.strip() if payload.contact_email else None
    if payload.notes is not None:
        update_data["notes"] = payload.notes.strip() if payload.notes else None
    if payload.ga4_property_id is not None:
        update_data["ga4_property_id"] = payload.ga4_property_id.strip() if payload.ga4_property_id else None
    if payload.ga4_property_name is not None:
        update_data["ga4_property_name"] = payload.ga4_property_name.strip() if payload.ga4_property_name else None
    if payload.gsc_site_url is not None:
        update_data["gsc_site_url"] = payload.gsc_site_url.strip() if payload.gsc_site_url else None
    if payload.google_account_email is not None:
        update_data["google_account_email"] = payload.google_account_email.strip() if payload.google_account_email else None
    if payload.google_refresh_token is not None:
        update_data["google_refresh_token"] = payload.google_refresh_token.strip() if payload.google_refresh_token else None
    if payload.semrush_project_id is not None:
        update_data["semrush_project_id"] = payload.semrush_project_id.strip() if payload.semrush_project_id else None
    if payload.is_default is not None:
        update_data["is_default"] = bool(payload.is_default)

    updated_record = None

    # Attempt Supabase update
    try:
        client = get_authenticated_client(user.access_token)
        if update_data.get("is_default"):
            try:
                client.table("clients").update({"is_default": False}).eq("user_id", user.id).execute()
            except Exception:
                pass

        res = client.table("clients").update(update_data).eq("id", client_id).eq("user_id", user.id).execute()
        if res.data and len(res.data) > 0:
            updated_record = res.data[0]
    except Exception as e:
        print(f"Notice: Supabase client update failed (fallback): {e}")

    # Update in local storage
    store = load_local_clients()
    user_clients = store.setdefault(user.id, [])
    found = False
    for i, c in enumerate(user_clients):
        if c.get("id") == client_id:
            if update_data.get("is_default"):
                for other in user_clients:
                    other["is_default"] = False
            user_clients[i].update(update_data)
            updated_record = user_clients[i]
            found = True
            break

    if found or updated_record:
        save_local_clients(store)
        return updated_record

    raise HTTPException(status_code=404, detail="Client not found")

@router.delete("/{client_id}")
async def delete_client(
    client_id: str,
    user: UserContext = Depends(get_current_user)
):
    """
    Deletes a client record belonging to the authenticated user from Supabase and local storage.
    Returns: Deletion success confirmation and deleted client ID.
    """
    try:
        client = get_authenticated_client(user.access_token)
        client.table("clients").delete().eq("id", client_id).eq("user_id", user.id).execute()
    except Exception as e:
        print(f"Notice: Supabase client delete failed: {e}")

    store = load_local_clients()
    if user.id in store:
        initial_len = len(store[user.id])
        store[user.id] = [c for c in store[user.id] if c.get("id") != client_id]
        if len(store[user.id]) != initial_len:
            save_local_clients(store)
            return {"success": True, "message": "Client deleted successfully", "id": client_id}

    return {"success": True, "message": "Client removed", "id": client_id}

# ==============================================================================
# CLIENT-SPECIFIC INTEGRATION ENDPOINTS
# ==============================================================================

@router.get("/{client_id}/integrations")
async def get_client_integrations(
    client_id: str,
    user: UserContext = Depends(get_current_user)
):
    """
    Fetches analytics connection status (GA4, GSC, SEMrush) and available properties for a specific client.
    Returns: Connection states, account emails, selected property IDs, and lists of accessible properties.
    """
    client_data = await get_client(client_id, user)
    
    # Resolve tokens independently for GA4 and Search Console
    ga4_token = await resolve_client_google_token(user, client_data, service="google-analytics")
    gsc_token = await resolve_client_google_token(user, client_data, service="search-console")

    available_ga4 = []
    available_gsc = []

    if ga4_token:
        try:
            available_ga4 = await fetch_ga4_properties(ga4_token)
        except Exception as e:
            print(f"Notice: fetch_ga4_properties in get_client_integrations failed: {e}")

    if gsc_token:
        try:
            available_gsc = await fetch_gsc_sites(gsc_token)
        except Exception as e:
            print(f"Notice: fetch_gsc_sites in get_client_integrations failed: {e}")

    ga4_prop = client_data.get("ga4_property_id")
    gsc_site = client_data.get("gsc_site_url")

    # Match property display name if available
    ga4_display = client_data.get("ga4_property_name")
    if not ga4_display and ga4_prop:
        for p in available_ga4:
            if p.get("propertyId") == ga4_prop or p.get("propertyId") == f"properties/{ga4_prop}":
                ga4_display = p.get("displayName")
                break

    has_active_google = bool(ga4_prop or gsc_site or ga4_token or gsc_token)

    return {
        "clientId": client_id,
        "clientName": client_data.get("name"),
        "website": client_data.get("website"),
        "google": {
            "isConnected": has_active_google,
            "accountEmail": client_data.get("google_account_email") or ("Connected Google Account" if has_active_google else None),
            "ga4": {
                "propertyId": ga4_prop,
                "propertyName": ga4_display or ga4_prop,
                "isAuthenticated": bool(ga4_token),
                "isConnected": bool(ga4_prop)
            },
            "gsc": {
                "siteUrl": gsc_site,
                "isAuthenticated": bool(gsc_token),
                "isConnected": bool(gsc_site)
            },
            "availableGa4Properties": available_ga4,
            "availableGscSites": available_gsc
        },
        "semrush": {
            "isConnected": bool(settings.SEMRUSH_API_KEY or client_data.get("semrush_project_id")),
            "projectId": client_data.get("semrush_project_id") or "Default Backend Key"
        }
    }

@router.put("/{client_id}/integrations")
async def update_client_integrations(
    client_id: str,
    payload: ClientIntegrationsUpdateRequest,
    user: UserContext = Depends(get_current_user)
):
    """
    Updates the connected Google Analytics 4 property and Google Search Console site for a specific client.
    Persists property identifiers and dedicated credentials in Supabase and local storage.
    Returns: The refreshed client integration status object.
    """
    client_data = await get_client(client_id, user)
    
    update_data: Dict[str, Any] = {
        "updated_at": datetime.utcnow().isoformat()
    }
    if payload.ga4_property_id is not None:
        update_data["ga4_property_id"] = payload.ga4_property_id.strip() if payload.ga4_property_id else None
    if payload.ga4_property_name is not None:
        update_data["ga4_property_name"] = payload.ga4_property_name.strip() if payload.ga4_property_name else None
    if payload.gsc_site_url is not None:
        update_data["gsc_site_url"] = payload.gsc_site_url.strip() if payload.gsc_site_url else None
    if payload.google_account_email is not None:
        update_data["google_account_email"] = payload.google_account_email.strip() if payload.google_account_email else None
    if payload.google_refresh_token is not None:
        update_data["google_refresh_token"] = payload.google_refresh_token.strip() if payload.google_refresh_token else None
    if payload.semrush_project_id is not None:
        update_data["semrush_project_id"] = payload.semrush_project_id.strip() if payload.semrush_project_id else None

    # Supabase update
    try:
        supabase = get_authenticated_client(user.access_token)
        supabase.table("clients").update(update_data).eq("id", client_id).eq("user_id", user.id).execute()
    except Exception as e:
        print(f"Notice: Supabase client integration update failed: {e}")

    # Local fallback update
    store = load_local_clients()
    for c in store.get(user.id, []):
        if c.get("id") == client_id:
            c.update(update_data)
            save_local_clients(store)
            break

    return await get_client_integrations(client_id, user)

@router.post("/{client_id}/integrations/disconnect")
async def disconnect_client_integration(
    client_id: str,
    payload: ClientIntegrationDisconnectRequest,
    user: UserContext = Depends(get_current_user)
):
    """
    Disconnects and resets integration IDs and Google credentials specifically for this client.
    Evicts cached tokens for the disconnected service from the in-memory cache.
    Returns: The refreshed client integration status object after disconnection.
    """
    client_data = await get_client(client_id, user)
    service = payload.service

    update_fields: Dict[str, Any] = {
        "updated_at": datetime.utcnow().isoformat()
    }

    if service == "google-analytics":
        update_fields["ga4_property_id"] = None
        update_fields["ga4_property_name"] = None
        TOKEN_CACHE.pop(f"{user.id}:{client_id}:google-analytics", None)
        # If GSC is also not configured, reset client google credentials completely
        if not client_data.get("gsc_site_url"):
            update_fields["google_account_email"] = None
            update_fields["google_refresh_token"] = None
            TOKEN_CACHE.pop(f"{user.id}:{client_id}:all", None)

    elif service == "search-console":
        update_fields["gsc_site_url"] = None
        TOKEN_CACHE.pop(f"{user.id}:{client_id}:search-console", None)
        # If GA4 is also not configured, reset client google credentials completely
        if not client_data.get("ga4_property_id"):
            update_fields["google_account_email"] = None
            update_fields["google_refresh_token"] = None
            TOKEN_CACHE.pop(f"{user.id}:{client_id}:all", None)

    else: # "all"
        update_fields["ga4_property_id"] = None
        update_fields["ga4_property_name"] = None
        update_fields["gsc_site_url"] = None
        update_fields["google_account_email"] = None
        update_fields["google_refresh_token"] = None
        TOKEN_CACHE.pop(f"{user.id}:{client_id}:google-analytics", None)
        TOKEN_CACHE.pop(f"{user.id}:{client_id}:search-console", None)
        TOKEN_CACHE.pop(f"{user.id}:{client_id}:all", None)

    # Supabase update
    try:
        supabase = get_authenticated_client(user.access_token)
        supabase.table("clients").update(update_fields).eq("id", client_id).eq("user_id", user.id).execute()
    except Exception as e:
        print(f"Notice: Supabase client disconnect update failed: {e}")

    # Local fallback update
    store = load_local_clients()
    for c in store.get(user.id, []):
        if c.get("id") == client_id:
            c.update(update_fields)
            save_local_clients(store)
            break

    return await get_client_integrations(client_id, user)

@router.post("/{client_id}/integrations/test")
async def test_client_integrations(
    client_id: str,
    user: UserContext = Depends(get_current_user)
):
    """
    Pings live Google Analytics 4 and Google Search Console APIs using the client's configured credentials.
    Strictly reports authentic real-time connectivity states without data fabrication.
    Returns: Ping test results for Google Auth, GA4, and Search Console.
    """
    client_data = await get_client(client_id, user)
    ga4_token = await resolve_client_google_token(user, client_data, service="google-analytics")
    gsc_token = await resolve_client_google_token(user, client_data, service="search-console")

    test_result = {
        "clientId": client_id,
        "testedAt": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "googleAuth": {
            "valid": bool(ga4_token or gsc_token),
            "message": "Google authorization active" if (ga4_token or gsc_token) else "No Google account authorization found"
        },
        "ga4": {
            "status": "disconnected",
            "hasData": False,
            "message": "No GA4 property configured"
        },
        "gsc": {
            "status": "disconnected",
            "hasData": False,
            "message": "No Search Console site configured"
        }
    }

    # 1. Test GA4 (Live Google Analytics API call)
    ga4_prop = client_data.get("ga4_property_id")
    if ga4_prop and ga4_token:
        prop_id_match = re.search(r"\b(\d{7,12})\b", ga4_prop)
        clean_prop = f"properties/{prop_id_match.group(1)}" if prop_id_match else ga4_prop
        try:
            live_ga = await fetch_ga4_full_dataset(access_token=ga4_token, property_id=clean_prop, days=30)
            if live_ga and live_ga.get("summary"):
                users_count = live_ga["summary"].get("users", 0)
                sessions_count = live_ga["summary"].get("sessions", 0)
                if sessions_count > 0 or users_count > 0:
                    test_result["ga4"] = {
                        "status": "live",
                        "hasData": True,
                        "sessions": sessions_count,
                        "users": users_count,
                        "message": f"Live data verified: {sessions_count} sessions, {users_count} users in the last 30 days."
                    }
                else:
                    test_result["ga4"] = {
                        "status": "no_data",
                        "hasData": False,
                        "sessions": 0,
                        "users": 0,
                        "message": "Connection authentic, but no traffic data recorded by GA4 in the last 30 days."
                    }
            else:
                test_result["ga4"] = {
                    "status": "no_data",
                    "hasData": False,
                    "message": "No data available from Google Analytics for this property."
                }
        except Exception as e:
            test_result["ga4"] = {
                "status": "error",
                "hasData": False,
                "message": f"Google Analytics API error: {str(e)}"
            }
    elif ga4_prop and not ga4_token:
        test_result["ga4"] = {
            "status": "error",
            "hasData": False,
            "message": "GA4 property configured but Google Analytics authorization is missing or expired."
        }

    # 2. Test GSC (Live Google Search Console API call)
    gsc_site = client_data.get("gsc_site_url")
    if gsc_site and gsc_token:
        try:
            live_gsc = await fetch_gsc_full_dataset(access_token=gsc_token, site_url=gsc_site, days=30)
            if live_gsc and live_gsc.get("summary"):
                clicks_count = live_gsc["summary"].get("clicks", 0)
                impressions_count = live_gsc["summary"].get("impressions", 0)
                if clicks_count > 0 or impressions_count > 0:
                    test_result["gsc"] = {
                        "status": "live",
                        "hasData": True,
                        "clicks": clicks_count,
                        "impressions": impressions_count,
                        "message": f"Live data verified: {clicks_count} clicks, {impressions_count} impressions in the last 30 days."
                    }
                else:
                    test_result["gsc"] = {
                        "status": "no_data",
                        "hasData": False,
                        "clicks": 0,
                        "impressions": 0,
                        "message": "Connection authentic, but no clicks or impressions recorded by Search Console in the last 30 days."
                    }
            else:
                test_result["gsc"] = {
                    "status": "no_data",
                    "hasData": False,
                    "message": "No data available from Search Console for this site."
                }
        except Exception as e:
            test_result["gsc"] = {
                "status": "error",
                "hasData": False,
                "message": f"Search Console API error: {str(e)}"
            }
    elif gsc_site and not gsc_token:
        test_result["gsc"] = {
            "status": "error",
            "hasData": False,
            "message": "Search Console site configured but Search Console authorization is missing or expired."
        }

    return test_result

