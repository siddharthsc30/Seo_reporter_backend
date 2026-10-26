from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime, timedelta
import asyncio
import time
import math
import json
import re
from fastapi import APIRouter, Query, Depends
from pydantic import BaseModel
from auth import get_optional_user, UserContext
from supabase_client import get_authenticated_client
from services.google_oauth import (
    refresh_google_access_token,
    get_service_account_access_token,
    get_scopes_for_service
)
from services.gsc_service import fetch_gsc_full_dataset, fetch_gsc_sites
from services.ga4_service import fetch_ga4_full_dataset, fetch_ga4_properties
from services.semrush_service import fetch_semrush_data
from routers.integrations import TOKEN_CACHE
from config import settings

router = APIRouter(prefix="/api/analytics", tags=["Analytics"])

import logging

logger = logging.getLogger(__name__)

# Global TTL cache (60 seconds) and in-flight promise coalescing
OVERVIEW_CACHE: Dict[str, Tuple[Dict[str, Any], float]] = {}
IN_FLIGHT_REQUESTS: Dict[str, asyncio.Task] = {}


def log_debug(msg: str):
    logger.debug(msg)

async def get_active_access_token(user: UserContext, service_name: str, integration_row: Dict[str, Any]) -> Optional[str]:
    """
    Resolves an active Google access token from cache, refresh token, or Service Account.
    Ensures cached tokens are unexpired (TTL-aware) and refreshes automatically if needed.
    """
    import time
    now_ts = time.time()
    cache_key = f"{user.id}:{service_name}"
    log_debug(f"get_active_access_token for user={user.id}, service={service_name}")
    
    cache_entry = TOKEN_CACHE.get(cache_key)
    if cache_entry and cache_entry.get("accessToken"):
        expires_at = cache_entry.get("expires_at", 0)
        if expires_at > now_ts:
            log_debug(f"Found active valid token in TOKEN_CACHE for {service_name} (expires in {int(expires_at - now_ts)}s)")
            return cache_entry["accessToken"]
        else:
            log_debug(f"Cached token for {service_name} is expired; evicting from TOKEN_CACHE")
            TOKEN_CACHE.pop(cache_key, None)

    # If Service Account is stored
    api_key = integration_row.get("api_key")
    if api_key and api_key.strip().startswith("{"):
        try:
            log_debug(f"Attempting Service Account auth for {service_name}")
            sa_dict = json.loads(api_key)
            scopes = get_scopes_for_service(service_name)
            token = await get_service_account_access_token(sa_dict, scopes)
            TOKEN_CACHE[cache_key] = {
                "accessToken": token,
                "expires_at": now_ts + 3500
            }
            log_debug(f"Successfully generated SA token for {service_name}")
            return token
        except Exception as e:
            log_debug(f"Service Account token fetch error for {service_name}: {e}")

    # If OAuth refresh token exists
    refresh_token = integration_row.get("refresh_token")
    log_debug(f"Checking refresh_token for {service_name}: exists={bool(refresh_token)}")
    if refresh_token:
        try:
            res = await refresh_google_access_token(refresh_token)
            new_access_token = res.get("access_token")
            if new_access_token:
                expires_in = float(res.get("expires_in", 3599))
                expires_at = now_ts + expires_in - 120  # 2 minute safety margin
                TOKEN_CACHE[cache_key] = {
                    "accessToken": new_access_token,
                    "refreshToken": refresh_token,
                    "expires_at": expires_at
                }
                log_debug(f"Successfully refreshed Google token for {service_name} (valid for {int(expires_in)}s)")
                return new_access_token
            else:
                log_debug(f"refresh_google_access_token returned no access_token: {res}")
        except Exception as e:
            log_debug(f"Failed to refresh Google token for {service_name}: {e}")

    log_debug(f"No active token could be resolved for {service_name}")
    return None

@router.get("/overview")
async def get_analytics_overview(
    domain: Optional[str] = Query(None, description="Domain name to analyze"),
    client_id: Optional[str] = Query(None, description="Client ID to analyze"),
    days: int = Query(30, description="Number of days to analyze (7, 30, 90)"),
    refresh: bool = Query(False, description="Bypass cache and force fresh fetch"),
    user: Optional[UserContext] = Depends(get_optional_user)
):
    """
    Returns full SEO analytics dataset.
    If authenticated and Google services are connected with valid credentials,
    fetches live data from Google Search Console and GA4; otherwise returns
    None for unauthenticated or disconnected sources. No data is fabricated.
    """
    clean_domain = domain.strip().lower() if domain else ""
    if clean_domain.startswith("http://"):
        clean_domain = clean_domain[7:]
    if clean_domain.startswith("https://"):
        clean_domain = clean_domain[8:]
    clean_domain = clean_domain.split("/")[0]

    user_id_prefix = user.id if user else "guest"
    cache_key = f"{user_id_prefix}:{client_id or ''}:{clean_domain}:{days}"

    # 1. Check in-memory cache
    if not refresh and cache_key in OVERVIEW_CACHE:
        cached_data, cached_at = OVERVIEW_CACHE[cache_key]
        if time.time() - cached_at < 60.0:
            log_debug(f"CACHE HIT for {cache_key} (age: {round(time.time() - cached_at, 1)}s)")
            return cached_data

    # 2. Check if identical request is currently in-flight (coalescing)
    if not refresh and cache_key in IN_FLIGHT_REQUESTS:
        task = IN_FLIGHT_REQUESTS[cache_key]
        if not task.done():
            log_debug(f"Awaiting in-flight task for {cache_key}")
            try:
                return await task
            except Exception as e:
                log_debug(f"In-flight task failed: {e}")

    # 3. Define the actual fetcher
    async def _fetch():
        dataset = {
            "domain": clean_domain,
            "periodDays": days,
            "generatedAt": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "googleAnalytics": None,
            "searchConsole": None,
            "semrush": None,
            "dataSources": {
                "googleAnalytics": "unavailable",
                "searchConsole": "unavailable",
                "semrush": "unavailable"
            }
        }

        if not clean_domain and not client_id:
            return dataset

        sem_api_key = settings.SEMRUSH_API_KEY
        client_record = None
        client = None

        if user:
            try:
                client = get_authenticated_client(user.access_token)
            except Exception as ex:
                log_debug(f"Notice: Supabase client init failed (using local fallback): {ex}")

            if client:
                if client_id:
                    try:
                        c_res = client.table("clients").select("*").eq("id", client_id).eq("user_id", user.id).execute()
                        if c_res.data and len(c_res.data) > 0:
                            client_record = c_res.data[0]
                    except Exception as ex:
                        log_debug(f"Could not fetch client by client_id in DB: {ex}")
                
                if not client_record and clean_domain:
                    try:
                        c_res = client.table("clients").select("*").eq("website", clean_domain).eq("user_id", user.id).execute()
                        if c_res.data and len(c_res.data) > 0:
                            client_record = c_res.data[0]
                    except Exception as ex:
                        log_debug(f"Could not fetch client by domain in DB: {ex}")

        # Check local storage fallback if client record wasn't found in DB (scoped strictly to authenticated user)
        if not client_record and user:
            from routers.clients import load_local_clients
            local_store = load_local_clients()
            if user.id in local_store:
                for c in local_store[user.id]:
                    if client_id and c.get("id") == client_id:
                        client_record = c
                        break
                    if clean_domain and c.get("website") == clean_domain:
                        client_record = c
                        break

        token_gsc = None
        token_ga4 = None
        target_gsc_site = None
        target_ga4_prop = None

        if client_record:
            target_gsc_site = client_record.get("gsc_site_url")
            target_ga4_prop = client_record.get("ga4_property_id")
            from routers.clients import resolve_client_google_token
            token_gsc = await resolve_client_google_token(user, client_record, service="search-console")
            token_ga4 = await resolve_client_google_token(user, client_record, service="google-analytics")
        elif user and client:
            integrations_map = {}
            try:
                res = client.table("integrations").select("*").eq("user_id", user.id).eq("is_connected", True).execute()
                rows = res.data or []
                integrations_map = {r.get("name"): r for r in rows}
            except Exception as db_err:
                log_debug(f"Notice: Supabase integrations query failed: {db_err}")

            target_gsc_site = integrations_map.get("search-console", {}).get("selected_property") or (f"sc-domain:{clean_domain}" if clean_domain else None)
            target_ga4_prop = integrations_map.get("google-analytics", {}).get("selected_property") or ""
            gsc_row = integrations_map.get("search-console", {})
            token_gsc = await get_active_access_token(user, "search-console", gsc_row) if gsc_row else None
            ga_row = integrations_map.get("google-analytics", {})
            token_ga4 = await get_active_access_token(user, "google-analytics", ga_row) if ga_row else None

            sem_row = integrations_map.get("semrush")
            if sem_row and sem_row.get("api_key"):
                sem_api_key = sem_row.get("api_key")

        # Auto-detect missing properties if account is connected but property isn't explicitly configured
        if token_gsc and not target_gsc_site:
            try:
                gsc_sites = await fetch_gsc_sites(token_gsc)
                verified = [s for s in gsc_sites if s.get("permissionLevel") in ("siteOwner", "siteFullUser", "siteRestrictedUser")]
                matched = next((s["siteUrl"] for s in verified if clean_domain and clean_domain in s["siteUrl"].lower()), None)
                if not matched and verified:
                    matched = verified[0]["siteUrl"]
                if matched:
                    target_gsc_site = matched
                    log_debug(f"Auto-resolved target GSC site: {target_gsc_site}")
            except Exception as ex:
                log_debug(f"GSC auto-detect site error: {ex}")

        if token_ga4 and not target_ga4_prop:
            try:
                ga_props = await fetch_ga4_properties(token_ga4)
                matched = next((p["propertyId"] for p in ga_props if clean_domain and clean_domain in p.get("displayName", "").lower()), None)
                if not matched and ga_props:
                    matched = ga_props[0]["propertyId"]
                if matched:
                    target_ga4_prop = matched
                    log_debug(f"Auto-resolved target GA4 property: {target_ga4_prop}")
            except Exception as ex:
                log_debug(f"GA4 auto-detect property error: {ex}")

        # Async tasks to run in parallel
        async def fetch_gsc():
            nonlocal target_gsc_site, token_gsc
            if not token_gsc or not target_gsc_site:
                return ("gsc", None, "no_token_or_site")
            try:
                log_debug(f"Starting GSC query for {target_gsc_site}")
                res = await fetch_gsc_full_dataset(access_token=token_gsc, site_url=target_gsc_site, days=days)
                return ("gsc", res, None)
            except Exception as ex:
                err_msg = str(ex)
                log_debug(f"GSC query initial attempt failed ({target_gsc_site}): {err_msg}")
                # If 401 / unauthenticated, token expired! Evict cache, refresh, and retry immediately
                if "401" in err_msg or "unauthenticated" in err_msg.lower():
                    log_debug(f"GSC 401 unauthenticated detected. Evicting token and retrying with refresh...")
                    if user:
                        TOKEN_CACHE.pop(f"{user.id}:search-console", None)
                        if client_record and client_record.get("id"):
                            TOKEN_CACHE.pop(f"{user.id}:{client_record.get('id')}:search-console", None)
                    fresh_tok = None
                    if client_record:
                        fresh_tok = await resolve_client_google_token(user, client_record, service="search-console")
                    elif user and client:
                        gsc_row = integrations_map.get("search-console", {})
                        fresh_tok = await get_active_access_token(user, "search-console", gsc_row)
                    if fresh_tok and fresh_tok != token_gsc:
                        token_gsc = fresh_tok
                        try:
                            log_debug(f"Retrying GSC with refreshed token for {target_gsc_site}")
                            retry_res = await fetch_gsc_full_dataset(access_token=token_gsc, site_url=target_gsc_site, days=days)
                            return ("gsc", retry_res, None)
                        except Exception as retry_err:
                            log_debug(f"GSC token refresh retry failed: {retry_err}")
                            err_msg = str(retry_err)

                # If 403 / permission error, check if an alternative verified site exists
                if ("permission" in err_msg.lower() or "403" in err_msg) and token_gsc:
                    try:
                        gsc_sites = await fetch_gsc_sites(token_gsc)
                        verified = [s["siteUrl"] for s in gsc_sites if s.get("permissionLevel") in ("siteOwner", "siteFullUser", "siteRestrictedUser") and s["siteUrl"] != target_gsc_site]
                        if verified:
                            alt_site = verified[0]
                            log_debug(f"Retrying GSC with verified alternative: {alt_site}")
                            alt_res = await fetch_gsc_full_dataset(access_token=token_gsc, site_url=alt_site, days=days)
                            target_gsc_site = alt_site
                            return ("gsc", alt_res, None)
                    except Exception as alt_err:
                        log_debug(f"GSC alternative retry failed: {alt_err}")
                return ("gsc", None, err_msg)

        async def fetch_ga4():
            nonlocal token_ga4
            if not token_ga4 or not target_ga4_prop:
                return ("ga4", None, "no_token_or_prop")
            prop_id_match = re.search(r"\b(\d{7,12})\b", target_ga4_prop)
            prop_id = f"properties/{prop_id_match.group(1)}" if prop_id_match else target_ga4_prop
            try:
                log_debug(f"Starting GA4 query for {prop_id}")
                res = await fetch_ga4_full_dataset(access_token=token_ga4, property_id=prop_id, days=days)
                return ("ga4", res, None)
            except Exception as ex:
                err_msg = str(ex)
                log_debug(f"GA4 query initial attempt failed ({prop_id}): {err_msg}")
                # If 401 / unauthenticated, token expired! Evict cache, refresh, and retry immediately
                if "401" in err_msg or "unauthenticated" in err_msg.lower():
                    log_debug(f"GA4 401 unauthenticated detected for {prop_id}. Evicting token and retrying with refresh...")
                    if user:
                        TOKEN_CACHE.pop(f"{user.id}:google-analytics", None)
                        if client_record and client_record.get("id"):
                            TOKEN_CACHE.pop(f"{user.id}:{client_record.get('id')}:google-analytics", None)
                    fresh_tok = None
                    if client_record:
                        fresh_tok = await resolve_client_google_token(user, client_record, service="google-analytics")
                    elif user and client:
                        ga_row = integrations_map.get("google-analytics", {})
                        fresh_tok = await get_active_access_token(user, "google-analytics", ga_row)
                    if fresh_tok and fresh_tok != token_ga4:
                        token_ga4 = fresh_tok
                        try:
                            log_debug(f"Retrying GA4 with refreshed token for {prop_id}")
                            retry_res = await fetch_ga4_full_dataset(access_token=token_ga4, property_id=prop_id, days=days)
                            return ("ga4", retry_res, None)
                        except Exception as retry_err:
                            log_debug(f"GA4 token refresh retry failed: {retry_err}")
                            err_msg = str(retry_err)
                return ("ga4", None, err_msg)

        async def fetch_sem():
            if not sem_api_key or not clean_domain:
                return ("sem", None, "no_key_or_domain")
            try:
                log_debug(f"Starting SEMrush query for {clean_domain}")
                res = await fetch_semrush_data(sem_api_key, domain=clean_domain, days=days)
                return ("sem", res, None)
            except Exception as ex:
                return ("sem", None, str(ex))

        # Run GSC, GA4, and SEMrush in PARALLEL with generous timeouts
        gsc_future = asyncio.wait_for(fetch_gsc(), timeout=20.0)
        ga4_future = asyncio.wait_for(fetch_ga4(), timeout=20.0)
        sem_future = asyncio.wait_for(fetch_sem(), timeout=10.0)

        results = await asyncio.gather(gsc_future, ga4_future, sem_future, return_exceptions=True)

        # Process GSC result
        gsc_res = results[0]
        if isinstance(gsc_res, tuple) and gsc_res[1]:
            dataset["searchConsole"] = gsc_res[1]
            dataset["dataSources"]["searchConsole"] = "live"
            log_debug("Parallel GSC SUCCESS")
        elif isinstance(gsc_res, tuple) and gsc_res[2] and gsc_res[2] != "no_token_or_site":
            err_text = str(gsc_res[2])
            dataset["searchConsoleError"] = err_text
            if "permission" in err_text.lower() or "403" in err_text:
                dataset["dataSources"]["searchConsole"] = "unverified_property"
            else:
                dataset["dataSources"]["searchConsole"] = "error"
        elif isinstance(gsc_res, Exception):
            log_debug(f"GSC task exception: {gsc_res}")
            dataset["dataSources"]["searchConsole"] = "timeout" if isinstance(gsc_res, asyncio.TimeoutError) else "error"

        # Process GA4 result
        ga4_res = results[1]
        if isinstance(ga4_res, tuple) and ga4_res[1]:
            dataset["googleAnalytics"] = ga4_res[1]
            dataset["dataSources"]["googleAnalytics"] = "live"
            log_debug("Parallel GA4 SUCCESS")
        elif isinstance(ga4_res, tuple) and ga4_res[2] and ga4_res[2] != "no_token_or_prop":
            log_debug(f"GA4 query error: {ga4_res[2]}")
            err_text = str(ga4_res[2])
            dataset["googleAnalyticsError"] = err_text
            if "permission" in err_text.lower() or "403" in err_text:
                dataset["dataSources"]["googleAnalytics"] = "unverified_property"
            else:
                dataset["dataSources"]["googleAnalytics"] = "error"
        elif isinstance(ga4_res, Exception):
            log_debug(f"GA4 task exception: {ga4_res}")
            dataset["dataSources"]["googleAnalytics"] = "timeout" if isinstance(ga4_res, asyncio.TimeoutError) else "error"

        # Process SEMrush result
        sem_res = results[2]
        if isinstance(sem_res, tuple) and sem_res[1]:
            dataset["semrush"] = sem_res[1]
            dataset["dataSources"]["semrush"] = "live"
            log_debug("Parallel SEMrush SUCCESS")
        elif sem_api_key:
            dataset["dataSources"]["semrush"] = "configured"

        log_debug(f"<<< RETURNING DATASET: GA={bool(dataset['googleAnalytics'])}, GSC={bool(dataset['searchConsole'])}, SEM={bool(dataset['semrush'])}")
        return dataset

    # In-flight task creation
    task = asyncio.create_task(_fetch())
    IN_FLIGHT_REQUESTS[cache_key] = task

    try:
        data = await task
        OVERVIEW_CACHE[cache_key] = (data, time.time())
        return data
    finally:
        IN_FLIGHT_REQUESTS.pop(cache_key, None)

