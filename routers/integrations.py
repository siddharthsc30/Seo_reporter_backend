from typing import Optional, List, Dict, Any
from datetime import datetime
import json
import uuid
import time
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel
from auth import get_current_user, get_optional_user, UserContext
from supabase_client import get_authenticated_client, get_supabase_client
from config import settings
from services.google_oauth import (
    build_google_auth_url,
    exchange_google_code,
    refresh_google_access_token,
    fetch_google_user_info,
    get_service_account_access_token,
    get_scopes_for_service
)
from services.gsc_service import fetch_gsc_sites
from services.ga4_service import fetch_ga4_properties

router = APIRouter(prefix="/api/integrations", tags=["Integrations"])

class ConnectIntegrationRequest(BaseModel):
    name: str # 'google-analytics' | 'search-console' | 'semrush'
    account_name: Optional[str] = None
    selected_property: Optional[str] = None
    api_key: Optional[str] = None
    refresh_token: Optional[str] = None

class DisconnectIntegrationRequest(BaseModel):
    name: str

class GoogleCallbackRequest(BaseModel):
    code: str
    service: Optional[str] = "all" # 'all' | 'google-analytics' | 'search-console'
    redirect_uri: Optional[str] = None
    client_id: Optional[str] = None

class SelectPropertyRequest(BaseModel):
    service: str # 'google-analytics' | 'search-console'
    property: str

class ServiceAccountRequest(BaseModel):
    service: str # 'all' | 'google-analytics' | 'search-console'
    service_account_json: str # JSON string

DEFAULT_INTEGRATIONS = {
    "google-analytics": {"isConnected": False},
    "search-console": {"isConnected": False},
    "semrush": {"isConnected": False}
}

# In-memory token cache to minimize database/OAuth roundtrips
TOKEN_CACHE: Dict[str, Dict[str, Any]] = {}

@router.get("")
@router.get("/")
@router.get("/status")
async def get_integrations_status(
    user: Optional[UserContext] = Depends(get_optional_user)
):
    """
    Returns the user's active integrations from Supabase.
    If SEMrush API key is configured in backend/.env, automatically provides it.
    """
    semrush_info = {"isConnected": False}
    if settings.SEMRUSH_API_KEY:
        masked_key = f"{settings.SEMRUSH_API_KEY[:10]}...{settings.SEMRUSH_API_KEY[-6:]}"
        semrush_info = {
            "isConnected": True,
            "accountName": "SEMrush Personal Token (.env)",
            "selectedProperty": "Global Search Analytics",
            "apiKey": masked_key,
            "hasRefreshToken": False,
            "connectedAt": "Active (.env)",
            "propertyList": []
        }

    if not user:
        return {
            "google-analytics": {"isConnected": False},
            "search-console": {"isConnected": False},
            "semrush": semrush_info
        }

    client = get_authenticated_client(user.access_token)
    try:
        res = client.table("integrations").select("*").eq("user_id", user.id).execute()
        rows = res.data or []
        
        result = {
            "google-analytics": {"isConnected": False},
            "search-console": {"isConnected": False},
            "semrush": {"isConnected": False}
        }
        for r in rows:
            name = r.get("name")
            if name in result:
                # Retrieve cached property list if available
                cached_props = TOKEN_CACHE.get(f"{user.id}:{name}", {}).get("propertyList")
                property_list = cached_props or []

                result[name] = {
                    "isConnected": r.get("is_connected", False),
                    "accountName": r.get("account_name"),
                    "selectedProperty": r.get("selected_property"),
                    "apiKey": r.get("api_key"),
                    "hasRefreshToken": bool(r.get("refresh_token")),
                    "connectedAt": r.get("connected_at"),
                    "propertyList": property_list
                }

        # If SEMrush API key is configured in backend/.env, automatically populate & persist it
        if settings.SEMRUSH_API_KEY and not result["semrush"].get("isConnected"):
            result["semrush"] = semrush_info
            try:
                now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                exist = client.table("integrations").select("id").eq("user_id", user.id).eq("name", "semrush").execute()
                sem_record = {
                    "user_id": user.id,
                    "name": "semrush",
                    "is_connected": True,
                    "account_name": "SEMrush Personal Token (.env)",
                    "selected_property": "Global Search Analytics",
                    "api_key": semrush_info["apiKey"],
                    "connected_at": now_str,
                    "updated_at": now_str
                }
                if exist.data and len(exist.data) > 0:
                    client.table("integrations").update(sem_record).eq("id", exist.data[0]["id"]).execute()
                else:
                    client.table("integrations").insert({**sem_record, "id": str(uuid.uuid4())}).execute()
            except Exception as auto_err:
                print(f"Notice: Could not auto-sync SEMrush to DB: {auto_err}")

        return result
    except Exception as e:
        print(f"Failed to fetch integrations for user {user.id}: {e}")
        return {
            "google-analytics": {"isConnected": False},
            "search-console": {"isConnected": False},
            "semrush": semrush_info
        }

@router.get("/google/auth-url")
async def get_google_auth_url(
    service: str = Query("all", description="Service name: all, google-analytics, search-console"),
    redirect_uri: Optional[str] = None,
    client_id: Optional[str] = Query(None),
    user: Optional[UserContext] = Depends(get_optional_user)
):
    """
    Generates a Google OAuth 2.0 authorization URL for popup or redirect flow.
    Encodes service scopes, state parameter (with user and optional client ID), and redirect URI.
    Returns: Formatted Google OAuth authorization URL and configuration status.
    """
    is_configured = bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET)
    user_state = f"{user.id if user else 'guest'}:{service}:{client_id or ''}"
    auth_url = build_google_auth_url(
        service=service, 
        state=user_state, 
        redirect_uri=redirect_uri
    )
    
    return {
        "authUrl": auth_url,
        "isConfigured": is_configured,
        "clientIdConfigured": bool(settings.GOOGLE_CLIENT_ID),
        "service": service,
        "clientId": client_id,
        "redirectUri": redirect_uri or settings.GOOGLE_REDIRECT_URI
    }

@router.post("/google/callback")
async def handle_google_callback(
    payload: GoogleCallbackRequest,
    user: UserContext = Depends(get_current_user)
):
    """
    Exchanges a Google OAuth authorization code for access and refresh tokens.
    Fetches accessible GA4 properties and GSC sites, and persists tokens to Supabase.
    Returns: Connection status, user email, and lists of discovered properties.
    """
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    client = get_authenticated_client(user.access_token)

    # Disallow fake demo/mock codes
    if payload.code.startswith("demo_") or payload.code.startswith("mock_"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Demo / Sandbox mode is disabled. Fabricated data is not permitted."
        )

    if not settings.GOOGLE_CLIENT_ID:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google Client ID not configured. Please configure Google OAuth credentials or use Service Account authentication."
        )

    # Real Google OAuth Exchange
    try:
        tokens = await exchange_google_code(payload.code, redirect_uri=payload.redirect_uri)
        access_token = tokens.get("access_token")
        refresh_token = tokens.get("refresh_token")

        if not access_token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No access token received from Google."
            )

        # Get authenticated user profile info
        user_info = await fetch_google_user_info(access_token)
        account_email = user_info.get("email") or user.email

        target_services = ["google-analytics", "search-console"] if payload.service == "all" else [payload.service]
        results = {}

        expires_in = float(tokens.get("expires_in", 3599))
        token_expires_at = time.time() + expires_in - 120  # 2 minute safety margin

        # Process Google Search Console
        if "search-console" in target_services:
            gsc_properties = []
            try:
                sites = await fetch_gsc_sites(access_token)
                verified_sites = [
                    s["siteUrl"] for s in sites 
                    if s.get("siteUrl") and s.get("permissionLevel") in ["siteOwner", "siteFullUser", "siteRestrictedUser"]
                ]
                gsc_properties = verified_sites or [s["siteUrl"] for s in sites if s.get("siteUrl")]
            except Exception as ex:
                print(f"Failed to fetch real GSC sites: {ex}")
                gsc_properties = []

            selected_gsc = gsc_properties[0] if gsc_properties else ""
            gsc_record = {
                "user_id": user.id,
                "name": "search-console",
                "is_connected": True,
                "account_name": account_email,
                "selected_property": selected_gsc,
                "connected_at": now_str,
                "updated_at": now_str
            }
            if refresh_token:
                gsc_record["refresh_token"] = refresh_token

            # Save in user-level integrations table and cache only if not a client-specific connection
            if not payload.client_id:
                exist = client.table("integrations").select("id").eq("user_id", user.id).eq("name", "search-console").execute()
                if exist.data and len(exist.data) > 0:
                    client.table("integrations").update(gsc_record).eq("id", exist.data[0]["id"]).execute()
                else:
                    client.table("integrations").insert({**gsc_record, "id": str(uuid.uuid4())}).execute()

                TOKEN_CACHE[f"{user.id}:search-console"] = {
                    "accessToken": access_token,
                    "refreshToken": refresh_token,
                    "expires_at": token_expires_at,
                    "propertyList": gsc_properties,
                    "accountEmail": account_email
                }

            results["search-console"] = {
                "name": "search-console",
                "isConnected": True,
                "accountName": account_email,
                "selectedProperty": selected_gsc,
                "propertyList": gsc_properties,
                "connectedAt": now_str
            }

        # Process Google Analytics 4
        if "google-analytics" in target_services:
            ga4_props_formatted = []
            try:
                props = await fetch_ga4_properties(access_token)
                ga4_props_formatted = [p["displayName"] for p in props if p.get("displayName")]
            except Exception as ex:
                print(f"Failed to fetch real GA4 properties: {ex}")
                ga4_props_formatted = []

            selected_ga4 = ga4_props_formatted[0] if ga4_props_formatted else ""
            ga4_record = {
                "user_id": user.id,
                "name": "google-analytics",
                "is_connected": True,
                "account_name": account_email,
                "selected_property": selected_ga4,
                "connected_at": now_str,
                "updated_at": now_str
            }
            if refresh_token:
                ga4_record["refresh_token"] = refresh_token

            # Save in user-level integrations table and cache only if not a client-specific connection
            if not payload.client_id:
                exist = client.table("integrations").select("id").eq("user_id", user.id).eq("name", "google-analytics").execute()
                if exist.data and len(exist.data) > 0:
                    client.table("integrations").update(ga4_record).eq("id", exist.data[0]["id"]).execute()
                else:
                    client.table("integrations").insert({**ga4_record, "id": str(uuid.uuid4())}).execute()

                TOKEN_CACHE[f"{user.id}:google-analytics"] = {
                    "accessToken": access_token,
                    "refreshToken": refresh_token,
                    "expires_at": token_expires_at,
                    "propertyList": ga4_props_formatted,
                    "accountEmail": account_email
                }

            results["google-analytics"] = {
                "name": "google-analytics",
                "isConnected": True,
                "accountName": account_email,
                "selectedProperty": selected_ga4,
                "propertyList": ga4_props_formatted,
                "connectedAt": now_str
            }

        # If client_id provided, sync credentials and cache specifically for that client
        if payload.client_id:
            try:
                from routers.clients import load_local_clients, save_local_clients
                store = load_local_clients()
                user_clients = store.get(user.id, [])
                for c in user_clients:
                    if c.get("id") == payload.client_id:
                        if refresh_token:
                            c["google_refresh_token"] = refresh_token
                        c["google_account_email"] = account_email
                        save_local_clients(store)
                        break
            except Exception as ex:
                print(f"Notice: local client credential sync failed: {ex}")

            try:
                up_fields = {"google_account_email": account_email}
                if refresh_token:
                    up_fields["google_refresh_token"] = refresh_token
                client.table("clients").update(up_fields).eq("id", payload.client_id).eq("user_id", user.id).execute()
            except Exception as ex:
                print(f"Notice: Supabase client credential sync failed: {ex}")

            for s_name in target_services:
                TOKEN_CACHE[f"{user.id}:{payload.client_id}:{s_name}"] = {
                    "accessToken": access_token,
                    "refreshToken": refresh_token,
                    "expires_at": token_expires_at,
                    "accountEmail": account_email
                }

        return {
            "success": True,
            "mode": "live",
            "message": "Connected to Google successfully!",
            "integrations": results
        }

    except Exception as e:
        print(f"Error handling Google OAuth callback: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Google authentication failed: {str(e)}"
        )

@router.post("/google/service-account")
async def connect_service_account(
    payload: ServiceAccountRequest,
    user: UserContext = Depends(get_current_user)
):
    """
    Validates and connects a Google Service Account JSON key.
    Enables access to Google Search Console and Google Analytics 4.
    """
    try:
        sa_dict = json.loads(payload.service_account_json)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON format in service account credentials."
        )

    client_email = sa_dict.get("client_email")
    if not client_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing 'client_email' in service account JSON."
        )

    target_services = ["google-analytics", "search-console"] if payload.service == "all" else [payload.service]
    results = {}
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    client = get_authenticated_client(user.access_token)

    for s_name in target_services:
        scopes = get_scopes_for_service(s_name)
        try:
            sa_token = await get_service_account_access_token(sa_dict, scopes)
        except Exception as ex:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Service Account authorization failed for {s_name}: {str(ex)}"
            )

        props = []
        if s_name == "search-console":
            try:
                sites = await fetch_gsc_sites(sa_token)
                props = [s["siteUrl"] for s in sites if s.get("siteUrl")]
            except Exception:
                props = []
        elif s_name == "google-analytics":
            try:
                p_list = await fetch_ga4_properties(sa_token)
                props = [p["displayName"] for p in p_list if p.get("displayName")]
            except Exception:
                props = []

        sel_prop = props[0] if props else ""
        record = {
            "user_id": user.id,
            "name": s_name,
            "is_connected": True,
            "account_name": f"SA: {client_email}",
            "selected_property": sel_prop,
            "api_key": payload.service_account_json, # store encrypted/raw SA config
            "connected_at": now_str,
            "updated_at": now_str
        }

        exist = client.table("integrations").select("id").eq("user_id", user.id).eq("name", s_name).execute()
        if exist.data and len(exist.data) > 0:
            client.table("integrations").update(record).eq("id", exist.data[0]["id"]).execute()
        else:
            client.table("integrations").insert({**record, "id": str(uuid.uuid4())}).execute()

        TOKEN_CACHE[f"{user.id}:{s_name}"] = {
            "accessToken": sa_token,
            "serviceAccount": sa_dict,
            "propertyList": props,
            "accountEmail": client_email,
            "expires_at": time.time() + 3500
        }
        results[s_name] = {
            "name": s_name,
            "isConnected": True,
            "accountName": f"SA: {client_email}",
            "selectedProperty": sel_prop,
            "propertyList": props,
            "connectedAt": now_str
        }

    return {
        "success": True,
        "message": f"Service Account connected for {payload.service}",
        "integrations": results
    }

@router.post("/google/select-property")
async def select_google_property(
    payload: SelectPropertyRequest,
    user: UserContext = Depends(get_current_user)
):
    """
    Updates the active selected site/property for GSC or GA4.
    """
    if payload.service not in ["google-analytics", "search-console"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported service name."
        )

    client = get_authenticated_client(user.access_token)
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    try:
        client.table("integrations").update({
            "selected_property": payload.property,
            "updated_at": now_str
        }).eq("user_id", user.id).eq("name", payload.service).execute()

        return {
            "success": True,
            "service": payload.service,
            "selectedProperty": payload.property
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update selected property: {str(e)}"
        )

@router.post("/connect")
async def connect_integration(
    payload: ConnectIntegrationRequest,
    user: UserContext = Depends(get_current_user)
):
    """
    Connects or updates an integration (e.g. SEMrush or manual settings) in Supabase.
    """
    if payload.name not in ["google-analytics", "search-console", "semrush"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported integration name: {payload.name}"
        )

    client = get_authenticated_client(user.access_token)
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    try:
        existing = client.table("integrations").select("id").eq("user_id", user.id).eq("name", payload.name).execute()
        
        record = {
            "user_id": user.id,
            "name": payload.name,
            "is_connected": True,
            "account_name": payload.account_name or f"{payload.name.title()} Account",
            "selected_property": payload.selected_property,
            "api_key": payload.api_key,
            "connected_at": now_str,
            "updated_at": now_str
        }
        if payload.refresh_token:
            record["refresh_token"] = payload.refresh_token

        if existing.data and len(existing.data) > 0:
            rec_id = existing.data[0]["id"]
            client.table("integrations").update(record).eq("id", rec_id).execute()
        else:
            client.table("integrations").insert({**record, "id": str(uuid.uuid4())}).execute()

        return {
            "success": True,
            "message": f"Successfully connected {payload.name}",
            "integration": {
                "name": payload.name,
                "isConnected": True,
                "accountName": record["account_name"],
                "selectedProperty": record["selected_property"],
                "connectedAt": record["connected_at"],
                "propertyList": []
            }
        }
    except Exception as e:
        print(f"Error saving integration to Supabase: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error saving integration: {str(e)}"
        )

@router.post("/disconnect")
async def disconnect_integration(
    payload: DisconnectIntegrationRequest,
    user: UserContext = Depends(get_current_user)
):
    """
    Disconnects an integration for the authenticated user in Supabase.
    """
    client = get_authenticated_client(user.access_token)
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    
    try:
        client.table("integrations").update({
            "is_connected": False,
            "updated_at": now_str
        }).eq("user_id", user.id).eq("name", payload.name).execute()

        # Clear token cache
        TOKEN_CACHE.pop(f"{user.id}:{payload.name}", None)

        return {
            "success": True,
            "message": f"Disconnected {payload.name}"
        }
    except Exception as e:
        print(f"Error disconnecting integration in Supabase: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error disconnecting integration: {str(e)}"
        )

@router.get("/{name}/properties")
async def get_integration_properties(
    name: str,
    user: Optional[UserContext] = Depends(get_optional_user)
):
    """
    Returns available properties (GA4 accounts or Search Console verified sites) for an integration provider.
    Returns: Provider name and array of accessible property strings.
    """
    if user:
        cached = TOKEN_CACHE.get(f"{user.id}:{name}", {}).get("propertyList")
        if cached:
            return {"name": name, "properties": cached}

    return {
        "name": name,
        "properties": []
    }
