import calendar
from datetime import datetime, date, timedelta
from typing import Dict, Any, Tuple, Optional, List
import re
from fastapi import HTTPException, status

from services.google_oauth import (
    refresh_google_access_token,
    get_service_account_access_token,
    get_scopes_for_service
)
from services.ga4_service import fetch_ga4_full_dataset
from services.gsc_service import fetch_gsc_full_dataset
from services.semrush_service import fetch_semrush_data
from config import settings

def calc_delta(curr: float, prior: float, is_position: bool = False) -> Tuple[str, str, float]:
    """
    Computes percentage or positional delta between current and prior periods.
    Returns: (delta_label, direction, numeric_delta)
    direction: 'up' | 'down' | 'flat'
    """
    if is_position:
        diff = round(curr - prior, 1)
        if diff < -0.1:
            return (f"{diff:+.1f} pos (improved)", "up", diff)
        elif diff > 0.1:
            return (f"{diff:+.1f} pos (declined)", "down", diff)
        return ("0.0 pos (steady)", "flat", 0.0)

    if prior <= 0:
        if curr > 0:
            return ("+100% MoM", "up", 100.0)
        return ("0% MoM", "flat", 0.0)

    diff = curr - prior
    pct = (diff / prior) * 100.0
    direction = "up" if pct > 0.5 else "down" if pct < -0.5 else "flat"
    sign = "+" if pct > 0 else ""
    return (f"{sign}{pct:.1f}% MoM", direction, pct)

def get_period_slice(
    month_str: Optional[str] = None,
    start_date_str: Optional[str] = None,
    end_date_str: Optional[str] = None
) -> Dict[str, Any]:
    """
    Parses 'YYYY-MM' (e.g. '2026-08') or custom start_date and end_date
    (or custom string 'YYYY-MM-DD_to_YYYY-MM-DD') and generates day-matched
    date ranges for current period and prior comparison period.
    """
    is_custom_range = False
    curr_start = None
    curr_end = None

    # 1. Check if explicit start_date_str and end_date_str are supplied
    if start_date_str and end_date_str:
        try:
            curr_start = datetime.strptime(start_date_str.strip(), "%Y-%m-%d").date()
            curr_end = datetime.strptime(end_date_str.strip(), "%Y-%m-%d").date()
            is_custom_range = True
        except Exception:
            pass

    # 2. Check if month_str is actually a composite custom date string like "2026-08-01_to_2026-08-15"
    if not is_custom_range and month_str:
        m_str = month_str.strip()
        if "_to_" in m_str:
            parts = m_str.split("_to_")
            try:
                curr_start = datetime.strptime(parts[0].strip(), "%Y-%m-%d").date()
                curr_end = datetime.strptime(parts[1].strip(), "%Y-%m-%d").date()
                is_custom_range = True
            except Exception:
                pass
        elif re.match(r"^\d{4}-\d{2}-\d{2}_\d{4}-\d{2}-\d{2}$", m_str):
            parts = m_str.split("_")
            try:
                curr_start = datetime.strptime(parts[0].strip(), "%Y-%m-%d").date()
                curr_end = datetime.strptime(parts[1].strip(), "%Y-%m-%d").date()
                is_custom_range = True
            except Exception:
                pass
        elif re.match(r"^\d{4}-\d{2}-\d{2}$", m_str):
            try:
                curr_start = datetime.strptime(m_str, "%Y-%m-%d").date()
                curr_end = curr_start
                is_custom_range = True
            except Exception:
                pass

    def ordinal(n: int) -> str:
        if 11 <= (n % 100) <= 13:
            return f"{n}th"
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
        return f"{n}{suffix}"

    if is_custom_range and curr_start and curr_end:
        if curr_end < curr_start:
            curr_start, curr_end = curr_end, curr_start

        days_count = (curr_end - curr_start).days + 1

        # Check if selection is aligned with a calendar month (starting on 1st of month)
        if curr_start.day == 1 and curr_start.month == curr_end.month and curr_start.year == curr_end.year:
            if curr_start.month == 1:
                prior_year = curr_start.year - 1
                prior_month = 12
            else:
                prior_year = curr_start.year
                prior_month = curr_start.month - 1

            prior_days_in_month = calendar.monthrange(prior_year, prior_month)[1]
            curr_days_in_month = calendar.monthrange(curr_start.year, curr_start.month)[1]

            if curr_end.day == curr_days_in_month:
                # Full calendar month (e.g. Sep 1 - Sep 30) -> compare to full prior month (Aug 1 - Aug 31)
                prior_start = date(prior_year, prior_month, 1)
                prior_end = date(prior_year, prior_month, prior_days_in_month)
            else:
                # Month-to-date starting on 1st (e.g. Sep 1 - Sep 15) -> compare to Aug 1 - Aug 15
                prior_start = date(prior_year, prior_month, 1)
                prior_end = date(prior_year, prior_month, min(curr_end.day, prior_days_in_month))
        else:
            # Arbitrary date range -> shift back by exact days count
            prior_end = curr_start - timedelta(days=1)
            prior_start = prior_end - timedelta(days=days_count - 1)

        # Labels formatting
        if curr_start.month == curr_end.month and curr_start.year == curr_end.year:
            curr_month_name = curr_start.strftime("%B %Y")
            curr_month_short = curr_start.strftime("%b %Y")
            primary_period_label = f"{curr_start.day}–{curr_end.day} {curr_month_name}"
            curr_period_short = f"{curr_start.day}–{curr_end.day} {curr_month_short}"
        else:
            curr_month_name = f"{curr_start.strftime('%b %Y')} – {curr_end.strftime('%b %Y')}"
            curr_month_short = f"{curr_start.strftime('%b')}–{curr_end.strftime('%b %y')}"
            primary_period_label = f"{curr_start.day} {curr_start.strftime('%b %Y')} – {curr_end.day} {curr_end.strftime('%b %Y')}"
            curr_period_short = f"{curr_start.day} {curr_start.strftime('%b')} – {curr_end.day} {curr_end.strftime('%b %y')}"

        if prior_start.month == prior_end.month and prior_start.year == prior_end.year:
            prior_month_name = prior_start.strftime("%B %Y")
            prior_month_short = prior_start.strftime("%b %Y")
            comparison_period_label = f"{prior_start.day}–{prior_end.day} {prior_month_name}"
            prior_period_short = f"{prior_start.day}–{prior_end.day} {prior_month_short}"
        else:
            prior_month_name = f"{prior_start.strftime('%b %Y')} – {prior_end.strftime('%b %Y')}"
            prior_month_short = f"{prior_start.strftime('%b')}–{prior_end.strftime('%b %y')}"
            comparison_period_label = f"{prior_start.day} {prior_start.strftime('%b %Y')} – {prior_end.day} {prior_end.strftime('%b %Y')}"
            prior_period_short = f"{prior_start.day} {prior_start.strftime('%b')} – {prior_end.day} {prior_end.strftime('%b %y')}"

        primary_period_long = f"{ordinal(curr_start.day)} {curr_start.strftime('%B %Y')} to {ordinal(curr_end.day)} {curr_end.strftime('%B %Y')}"
        prior_period_long = f"{ordinal(prior_start.day)} {prior_start.strftime('%B %Y')} to {ordinal(prior_end.day)} {prior_end.strftime('%B %Y')}"

        curr_period_slash = f"{curr_start.month}/{curr_start.day}/{str(curr_start.year)[2:]} - {curr_end.month}/{curr_end.day}/{str(curr_end.year)[2:]}"
        prior_period_slash = f"{prior_start.month}/{prior_start.day}/{str(prior_start.year)[2:]} - {prior_end.month}/{prior_end.day}/{str(prior_end.year)[2:]}"
        period_label = f"{primary_period_label} (vs {comparison_period_label})"
        report_month_key = f"{curr_start.strftime('%Y-%m-%d')}_to_{curr_end.strftime('%Y-%m-%d')}"

        return {
            "report_month": report_month_key,
            "curr_month_label": curr_month_name,
            "prior_month_label": prior_month_name,
            "curr_month_short": curr_month_short,
            "prior_month_short": prior_month_short,
            "primary_period_label": primary_period_label,
            "comparison_period_label": comparison_period_label,
            "period_label": period_label,
            "primary_period_long": primary_period_long,
            "prior_period_long": prior_period_long,
            "curr_period_short": curr_period_short,
            "prior_period_short": prior_period_short,
            "curr_period_slash": curr_period_slash,
            "prior_period_slash": prior_period_slash,
            "curr_start_str": curr_start.strftime("%Y-%m-%d"),
            "curr_end_str": curr_end.strftime("%Y-%m-%d"),
            "prior_start_str": prior_start.strftime("%Y-%m-%d"),
            "prior_end_str": prior_end.strftime("%Y-%m-%d"),
            "days_compared": days_count
        }

    # Month mode (default)
    try:
        parts = (month_str or "").strip().split("-")
        year = int(parts[0])
        month = int(parts[1])
    except Exception:
        today = date.today()
        year = today.year
        month = today.month

    today = date.today()
    days_in_month = calendar.monthrange(year, month)[1]

    # Check if month is current/in-progress
    is_current_month = (year == today.year and month == today.month)
    if is_current_month:
        curr_end_day = max(1, today.day - 1)
    else:
        curr_end_day = days_in_month

    curr_start = date(year, month, 1)
    curr_end = date(year, month, curr_end_day)

    # Prior month calculation
    if month == 1:
        prior_year = year - 1
        prior_month = 12
    else:
        prior_year = year
        prior_month = month - 1

    prior_days_in_month = calendar.monthrange(prior_year, prior_month)[1]
    if not is_current_month and curr_end_day == days_in_month:
        # Full completed month (e.g. Sep 1 - Sep 30) -> compare to full prior month (Aug 1 - Aug 31)
        prior_end_day = prior_days_in_month
    else:
        prior_end_day = min(curr_end_day, prior_days_in_month)
    prior_start = date(prior_year, prior_month, 1)
    prior_end = date(prior_year, prior_month, prior_end_day)

    curr_month_name = curr_start.strftime("%B %Y")
    prior_month_name = prior_start.strftime("%B %Y")
    curr_month_short = curr_start.strftime("%b %Y")
    prior_month_short = prior_start.strftime("%b %Y")

    primary_period_long = f"1st {curr_start.strftime('%B %Y')} to {ordinal(curr_end_day)} {curr_start.strftime('%B %Y')}"
    prior_period_long = f"1st {prior_start.strftime('%B %Y')} to {ordinal(prior_end_day)} {prior_start.strftime('%B %Y')}"

    curr_period_short = f"1–{curr_end_day} {curr_month_short}"
    prior_period_short = f"1–{prior_end_day} {prior_month_short}"

    curr_period_slash = f"{curr_start.month}/1/{str(curr_start.year)[2:]} - {curr_end.month}/{curr_end.day}/{str(curr_end.year)[2:]}"
    prior_period_slash = f"{prior_start.month}/1/{str(prior_start.year)[2:]} - {prior_end.month}/{prior_end.day}/{str(prior_end.year)[2:]}"

    primary_period_label = f"1–{curr_end_day} {curr_month_name}"
    comparison_period_label = f"1–{prior_end_day} {prior_month_name}"
    period_label = f"{primary_period_label} (vs {comparison_period_label})"

    return {
        "report_month": f"{year:04d}-{month:02d}",
        "curr_month_label": curr_month_name,
        "prior_month_label": prior_month_name,
        "curr_month_short": curr_month_short,
        "prior_month_short": prior_month_short,
        "primary_period_label": primary_period_label,
        "comparison_period_label": comparison_period_label,
        "period_label": period_label,
        "primary_period_long": primary_period_long,
        "prior_period_long": prior_period_long,
        "curr_period_short": curr_period_short,
        "prior_period_short": prior_period_short,
        "curr_period_slash": curr_period_slash,
        "prior_period_slash": prior_period_slash,
        "curr_start_str": curr_start.strftime("%Y-%m-%d"),
        "curr_end_str": curr_end.strftime("%Y-%m-%d"),
        "prior_start_str": prior_start.strftime("%Y-%m-%d"),
        "prior_end_str": prior_end.strftime("%Y-%m-%d"),
        "days_compared": curr_end_day
    }

async def aggregate_monthly_seo_metrics(
    user_id: str,
    website: str,
    month_str: Optional[str] = None,
    company_name: Optional[str] = None,
    integrations_rows: Optional[List[Dict[str, Any]]] = None,
    user: Optional[Any] = None,
    allow_sandbox_fallback: bool = False,
    client_rec: Optional[Dict[str, Any]] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
) -> Dict[str, Any]:
    """
    Main Data Aggregation Pipeline for Monthly / Custom Date SEO Report:
    Extracts authentic metrics from connected services (GA4, GSC, SEMrush).
    If a service or metric is not available, it is left empty/null.
    ZERO fabricated or simulated data.
    """
    from routers.analytics import get_active_access_token

    clean_domain = website.replace("https://", "").replace("http://", "").split("/")[0].lower()
    comp_name = company_name or clean_domain.split(".")[0].capitalize()
    periods = get_period_slice(month_str=month_str, start_date_str=start_date, end_date_str=end_date)
    days_compared = periods.get("days_compared", 30)

    integrations_map = {}
    if integrations_rows:
        for r in integrations_rows:
            if r.get("is_connected"):
                integrations_map[r.get("name")] = r

    ga_connected = "google-analytics" in integrations_map or bool(client_rec and client_rec.get("ga4_property_id"))
    gsc_connected = "search-console" in integrations_map or bool(client_rec and client_rec.get("gsc_site_url"))
    sem_connected = bool("semrush" in integrations_map or settings.SEMRUSH_API_KEY)

    # 1. GA4 Extraction
    section_01_website_traffic = {
        "status": "connected" if ga_connected else "unavailable",
        "stat_strip": [],
        "channels": [],
        "countries": [],
        "landing_pages": []
    }
    if user:
        try:
            token = None
            selected_prop = None
            if client_rec:
                from routers.clients import resolve_client_google_token
                token = await resolve_client_google_token(user, client_rec, service="google-analytics")
                selected_prop = client_rec.get("ga4_property_id") or ""
            elif ga_connected:
                ga_row = integrations_map.get("google-analytics", {})
                token = await get_active_access_token(user, "google-analytics", ga_row)
                selected_prop = ga_row.get("selected_property") or ""

            if token and selected_prop:
                prop_match = re.search(r"\b(\d{7,12})\b", selected_prop)
                prop_id = f"properties/{prop_match.group(1)}" if prop_match else selected_prop
                if prop_id:
                    try:
                        live_ga = await fetch_ga4_full_dataset(
                            access_token=token,
                            property_id=prop_id,
                            days=days_compared,
                            start_date=periods.get("curr_start_str"),
                            end_date=periods.get("curr_end_str"),
                            prior_start_date=periods.get("prior_start_str"),
                            prior_end_date=periods.get("prior_end_str")
                        )
                    except Exception as ga_ex:
                        err_str = str(ga_ex)
                        if "401" in err_str or "unauthenticated" in err_str.lower():
                            print(f"Notice: GA4 401 unauthenticated for {prop_id}. Evicting cache and retrying...")
                            from routers.integrations import TOKEN_CACHE
                            if user:
                                TOKEN_CACHE.pop(f"{user.id}:google-analytics", None)
                                if client_rec and client_rec.get("id"):
                                    TOKEN_CACHE.pop(f"{user.id}:{client_rec.get('id')}:google-analytics", None)
                            fresh_token = None
                            if client_rec:
                                fresh_token = await resolve_client_google_token(user, client_rec, service="google-analytics")
                            elif ga_connected:
                                fresh_token = await get_active_access_token(user, "google-analytics", integrations_map.get("google-analytics", {}))
                            if fresh_token:
                                token = fresh_token
                                live_ga = await fetch_ga4_full_dataset(
                                    access_token=token,
                                    property_id=prop_id,
                                    days=days_compared,
                                    start_date=periods.get("curr_start_str"),
                                    end_date=periods.get("curr_end_str"),
                                    prior_start_date=periods.get("prior_start_str"),
                                    prior_end_date=periods.get("prior_end_str")
                                )
                        else:
                            raise ga_ex
                    if live_ga and live_ga.get("summary"):
                        summary = live_ga["summary"]
                        u_chg = summary.get("usersChange")
                        s_chg = summary.get("sessionsChange")
                        section_01_website_traffic["stat_strip"] = [
                            {
                                "label": "Total users",
                                "value": f"{summary.get('users', 0):,}",
                                "delta": f"{u_chg:+.1f}% MoM" if u_chg is not None else "",
                                "direction": "up" if (u_chg or 0) > 0 else "down" if (u_chg or 0) < 0 else "flat"
                            },
                            {
                                "label": "Sessions",
                                "value": f"{summary.get('sessions', 0):,}",
                                "delta": f"{s_chg:+.1f}% MoM" if s_chg is not None else "",
                                "direction": "up" if (s_chg or 0) > 0 else "down" if (s_chg or 0) < 0 else "flat"
                            },
                            {
                                "label": "Avg session duration",
                                "value": str(summary.get("avgSessionDuration", "0s")),
                                "delta": "",
                                "direction": "flat"
                            },
                            {
                                "label": "Engagement rate",
                                "value": f"{100 - summary.get('bounceRate', 0):.1f}%" if summary.get("bounceRate") is not None else "N/A",
                                "delta": "",
                                "direction": "flat"
                            }
                        ]
                        def is_excluded_channel(name):
                            if not name:
                                return False
                            clean = re.sub(r'[-_\s]+', ' ', str(name).strip().lower())
                            return clean in {"ai assistant", "cross network", "paid search", "display"} or clean.startswith("display ")

                        def is_organic_search_or_social(name):
                            if not name:
                                return False
                            clean = re.sub(r'[-_\s]+', ' ', str(name).strip().lower())
                            return clean in {
                                "organic search",
                                "organic social",
                                "social",
                                "organic (search + social)",
                                "organic search + social"
                            }

                        def merge_organic_overview_channels(items):
                            combined = None
                            first_idx = -1
                            for idx, item in enumerate(items):
                                c_name = item.get("channel") or item.get("name") or ""
                                if is_organic_search_or_social(c_name):
                                    if first_idx == -1:
                                        first_idx = idx
                                    if not combined:
                                        combined = {
                                            "channel": "Organic (Search + Social)",
                                            "name": "Organic (Search + Social)",
                                            "prior": dict(item.get("prior", {})),
                                            "current": dict(item.get("current", {}))
                                        }
                                    else:
                                        p1 = combined.get("prior", {})
                                        p2 = item.get("prior", {})
                                        p_tot = p1.get("totalUsers", 0) + p2.get("totalUsers", 0)
                                        p_new = p1.get("newUsers", 0) + p2.get("newUsers", 0)
                                        p_ret = p1.get("returningUsers", 0) + p2.get("returningUsers", 0)
                                        p_sess = p1.get("sessions", 0) + p2.get("sessions", 0)
                                        p_eng = p1.get("engagedSessions", 0) + p2.get("engagedSessions", 0)
                                        p_rate = round((p_eng / max(1, p_sess)) * 100, 2)
                                        combined["prior"] = {
                                            "periodLabel": p1.get("periodLabel") or p2.get("periodLabel") or "",
                                            "totalUsers": p_tot,
                                            "newUsers": p_new,
                                            "returningUsers": p_ret,
                                            "sessions": p_sess,
                                            "engagedSessions": p_eng,
                                            "engagementRate": p_rate
                                        }

                                        c1 = combined.get("current", {})
                                        c2 = item.get("current", {})
                                        c_tot = c1.get("totalUsers", 0) + c2.get("totalUsers", 0)
                                        c_new = c1.get("newUsers", 0) + c2.get("newUsers", 0)
                                        c_ret = c1.get("returningUsers", 0) + c2.get("returningUsers", 0)
                                        c_sess = c1.get("sessions", 0) + c2.get("sessions", 0)
                                        c_eng = c1.get("engagedSessions", 0) + c2.get("engagedSessions", 0)
                                        c_rate = round((c_eng / max(1, c_sess)) * 100, 2)
                                        combined["current"] = {
                                            "periodLabel": c1.get("periodLabel") or c2.get("periodLabel") or "",
                                            "totalUsers": c_tot,
                                            "newUsers": c_new,
                                            "returningUsers": c_ret,
                                            "sessions": c_sess,
                                            "engagedSessions": c_eng,
                                            "engagementRate": c_rate
                                        }

                            if not combined:
                                return items

                            result = []
                            for idx, item in enumerate(items):
                                c_name = item.get("channel") or item.get("name") or ""
                                if idx == first_idx:
                                    result.append(combined)
                                elif not is_organic_search_or_social(c_name):
                                    result.append(item)
                            return result

                        def merge_organic_fallback_channels(items):
                            combined = None
                            first_idx = -1
                            for idx, item in enumerate(items):
                                c_name = item.get("channel") or item.get("name") or ""
                                if is_organic_search_or_social(c_name):
                                    if first_idx == -1:
                                        first_idx = idx
                                    if not combined:
                                        combined = dict(item)
                                        combined["channel"] = "Organic (Search + Social)"
                                        combined["name"] = "Organic (Search + Social)"
                                    else:
                                        combined["sessions"] = (combined.get("sessions") or 0) + (item.get("sessions") or 0)
                            if not combined:
                                return items
                            result = []
                            for idx, item in enumerate(items):
                                c_name = item.get("channel") or item.get("name") or ""
                                if idx == first_idx:
                                    result.append(combined)
                                elif not is_organic_search_or_social(c_name):
                                    result.append(item)
                            return result

                        channels_list = []
                        for ch in live_ga.get("channels", []):
                            c_name = ch.get("name", "Other")
                            if not is_excluded_channel(c_name):
                                channels_list.append({
                                    "channel": c_name,
                                    "name": c_name,
                                    "curr_users": None,
                                    "prior_users": None,
                                    "sessions": ch.get("sessions") or ch.get("value", 0),
                                    "bounce_rate": None
                                })
                        section_01_website_traffic["channels"] = merge_organic_fallback_channels(channels_list)
                        section_01_website_traffic["countries"] = live_ga.get("countries", [])
                        raw_uso = live_ga.get("userSessionsOverview", [])
                        filtered_uso = [
                            ch for ch in raw_uso
                            if not is_excluded_channel(ch.get("channel") or ch.get("name"))
                        ]
                        section_01_website_traffic["user_sessions_overview"] = merge_organic_overview_channels(filtered_uso)
                        section_01_website_traffic["geographical_insights"] = live_ga.get("geographicalInsights", [])
                        section_01_website_traffic["user_engagement"] = live_ga.get("userEngagement", {})
                        section_01_website_traffic["traffic_over_time"] = live_ga.get("trafficOverTime", [])
        except Exception as ex:
            print(f"Notice: GA4 aggregation failed: {ex}")

    # 2. GSC Extraction
    section_02_google_search = {
        "status": "connected" if gsc_connected else "unavailable",
        "stat_strip": [],
        "top_queries": [],
        "top_pages": [],
        "trend": [],
        "summary": {}
    }
    if user:
        try:
            token_gsc = None
            site_url = None
            if client_rec:
                from routers.clients import resolve_client_google_token
                token_gsc = await resolve_client_google_token(user, client_rec, service="search-console")
                site_url = client_rec.get("gsc_site_url")
            elif gsc_connected:
                gsc_row = integrations_map.get("search-console", {})
                token_gsc = await get_active_access_token(user, "search-console", gsc_row)
                site_url = gsc_row.get("selected_property") or f"sc-domain:{clean_domain}"

            if token_gsc and site_url:
                try:
                    live_gsc = await fetch_gsc_full_dataset(
                        access_token=token_gsc,
                        site_url=site_url,
                        days=days_compared,
                        start_date=periods.get("curr_start_str"),
                        end_date=periods.get("curr_end_str"),
                        prior_start_date=periods.get("prior_start_str"),
                        prior_end_date=periods.get("prior_end_str")
                    )
                except Exception as gsc_ex:
                    err_str = str(gsc_ex)
                    if "401" in err_str or "unauthenticated" in err_str.lower():
                        print(f"Notice: GSC 401 unauthenticated for {site_url}. Evicting cache and retrying...")
                        from routers.integrations import TOKEN_CACHE
                        if user:
                            TOKEN_CACHE.pop(f"{user.id}:search-console", None)
                            if client_rec and client_rec.get("id"):
                                TOKEN_CACHE.pop(f"{user.id}:{client_rec.get('id')}:search-console", None)
                        fresh_gsc = None
                        if client_rec:
                            fresh_gsc = await resolve_client_google_token(user, client_rec, service="search-console")
                        elif gsc_connected:
                            fresh_gsc = await get_active_access_token(user, "search-console", integrations_map.get("search-console", {}))
                        if fresh_gsc:
                            token_gsc = fresh_gsc
                            live_gsc = await fetch_gsc_full_dataset(
                                access_token=token_gsc,
                                site_url=site_url,
                                days=days_compared,
                                start_date=periods.get("curr_start_str"),
                                end_date=periods.get("curr_end_str"),
                                prior_start_date=periods.get("prior_start_str"),
                                prior_end_date=periods.get("prior_end_str")
                            )
                    else:
                        raise gsc_ex
                if live_gsc and live_gsc.get("summary"):
                    summary = live_gsc["summary"]
                    c_chg = summary.get("clicksChange")
                    i_chg = summary.get("impressionsChange")
                    p_clicks = summary.get("priorClicks")
                    p_imp = summary.get("priorImpressions")
                    p_ctr = summary.get("priorCtr")
                    p_pos = summary.get("priorPosition")

                    section_02_google_search["summary"] = summary
                    section_02_google_search["stat_strip"] = [
                        {
                            "label": "Total clicks",
                            "value": f"{summary.get('clicks', 0):,}",
                            "prior_value": f"{p_clicks:,}" if p_clicks is not None else "—",
                            "delta": f"{c_chg:+.1f}% MoM" if c_chg is not None else "",
                            "direction": "up" if (c_chg or 0) > 0 else "down" if (c_chg or 0) < 0 else "flat"
                        },
                        {
                            "label": "Total impressions",
                            "value": f"{summary.get('impressions', 0):,}",
                            "prior_value": f"{p_imp:,}" if p_imp is not None else "—",
                            "delta": f"{i_chg:+.1f}% MoM" if i_chg is not None else "",
                            "direction": "up" if (i_chg or 0) > 0 else "down" if (i_chg or 0) < 0 else "flat"
                        },
                        {
                            "label": "Average CTR",
                            "value": summary.get("ctrFormatted") or f"{summary.get('ctr', 0):.2f}%",
                            "prior_value": summary.get("priorCtrFormatted") or (f"{p_ctr:.2f}%" if p_ctr is not None else "—"),
                            "delta": f"{summary.get('ctrChange', 0):+.2f}% MoM" if summary.get("ctrChange") is not None else "",
                            "direction": "up" if (summary.get("ctrChange") or 0) > 0 else "down" if (summary.get("ctrChange") or 0) < 0 else "flat"
                        },
                        {
                            "label": "Average position",
                            "value": f"{summary.get('position', 0):.1f}",
                            "prior_value": f"{p_pos:.1f}" if p_pos is not None else "—",
                            "delta": f"{summary.get('positionChange', 0):+.1f} pos" if summary.get("positionChange") is not None else "",
                            "direction": "up" if (summary.get("positionChange") or 0) < 0 else "down" if (summary.get("positionChange") or 0) > 0 else "flat"
                        }
                    ]
                    section_02_google_search["top_queries"] = live_gsc.get("keywords", [])
                    section_02_google_search["top_pages"] = live_gsc.get("pages", [])
                    section_02_google_search["trend"] = live_gsc.get("trend", [])
        except Exception as ex:
            print(f"Notice: GSC aggregation failed: {ex}")

    # 3. SEMrush Extraction
    section_03_keyword_visibility = {
        "status": "connected" if sem_connected else "unavailable",
        "title": "Keyword Rankings & SERP Visibility",
        "stat_strip": [],
        "distribution": [],
        "top_keywords": []
    }
    if sem_connected:
        sem_api_key = integrations_map.get("semrush", {}).get("api_key") or settings.SEMRUSH_API_KEY
        if sem_api_key:
            try:
                live_sem = await fetch_semrush_data(sem_api_key, clean_domain, days=days_compared)
                if live_sem:
                    summary = live_sem.get("summary", {})
                    org_kw = summary.get("organicKeywords")
                    if org_kw is not None and org_kw > 0:
                        section_03_keyword_visibility["stat_strip"].append({
                            "label": "Ranked Keywords",
                            "value": f"{org_kw:,}",
                            "delta": "",
                            "direction": "flat"
                        })
                    section_03_keyword_visibility["top_keywords"] = live_sem.get("keywords", [])
            except Exception as ex:
                print(f"Notice: SEMrush aggregation failed: {ex}")

    # 4. Authority & Backlink Profile (No fake scores or fake backlinks)
    section_04_authority_backlinks = {
        "status": "unavailable",
        "title": "Authority Score & Backlink Profile",
        "authority_score": None,
        "total_backlinks": None,
        "referring_domains": None,
        "stat_strip": [],
        "top_backlinks": []
    }

    # 5. Technical SEO & Site Health (No fake audits or fake scores)
    section_05_technical_seo = {
        "status": "unavailable",
        "title": "Technical SEO & Site Health",
        "overall_health_score": None,
        "stat_strip": [],
        "core_web_vitals": [],
        "audit_checklist": []
    }

    # 6. Third-Party SEO & Semrush Metrics (Pages 9, 10, 11)
    # Computes authentic keyword tiers & branded splits from GSC and SEMrush data
    top_queries_list = section_02_google_search.get("top_queries", [])
    pos_buckets_curr = {"top_1_3": 0, "top_4_10": 0, "top_11_20": 0, "top_21_50": 0, "top_51_100": 0}
    pos_buckets_prior = {"top_1_3": 0, "top_4_10": 0, "top_11_20": 0, "top_21_50": 0, "top_51_100": 0}
    brand_keywords_curr = 0
    non_brand_keywords_curr = 0
    brand_traffic_curr = 0
    non_brand_traffic_curr = 0
    brand_keywords_prior = 0
    non_brand_keywords_prior = 0
    brand_traffic_prior = 0
    non_brand_traffic_prior = 0

    brand_tokens = [t.lower() for t in (clean_domain.split(".")[0] + " " + comp_name).split() if len(t) > 2]

    for q in top_queries_list:
        pos = q.get("position")
        pos_prev = q.get("position_prev") or q.get("prev_position")
        clicks = int(q.get("clicks") or q.get("clicks_curr") or 0)
        clicks_prev = int(q.get("clicks_prev") or 0)
        query_text = (q.get("query") or q.get("keyword") or "").lower()

        if pos is not None:
            try:
                p_val = float(pos)
                if 1.0 <= p_val <= 3.0:
                    pos_buckets_curr["top_1_3"] += 1
                elif 3.0 < p_val <= 10.0:
                    pos_buckets_curr["top_4_10"] += 1
                elif 10.0 < p_val <= 20.0:
                    pos_buckets_curr["top_11_20"] += 1
                elif 20.0 < p_val <= 50.0:
                    pos_buckets_curr["top_21_50"] += 1
                elif 50.0 < p_val <= 100.0:
                    pos_buckets_curr["top_51_100"] += 1
            except Exception:
                pass

        if pos_prev is not None:
            try:
                p_prev_val = float(pos_prev)
                if 1.0 <= p_prev_val <= 3.0:
                    pos_buckets_prior["top_1_3"] += 1
                elif 3.0 < p_prev_val <= 10.0:
                    pos_buckets_prior["top_4_10"] += 1
                elif 10.0 < p_prev_val <= 20.0:
                    pos_buckets_prior["top_11_20"] += 1
                elif 20.0 < p_prev_val <= 50.0:
                    pos_buckets_prior["top_21_50"] += 1
                elif 50.0 < p_prev_val <= 100.0:
                    pos_buckets_prior["top_51_100"] += 1
            except Exception:
                pass

        is_branded = any(tok in query_text for tok in brand_tokens) if brand_tokens else False
        if is_branded:
            brand_keywords_curr += 1
            brand_traffic_curr += clicks
            if clicks_prev > 0 or (pos_prev and float(pos_prev) > 0):
                brand_keywords_prior += 1
                brand_traffic_prior += clicks_prev
        else:
            non_brand_keywords_curr += 1
            non_brand_traffic_curr += clicks
            if clicks_prev > 0 or (pos_prev and float(pos_prev) > 0):
                non_brand_keywords_prior += 1
                non_brand_traffic_prior += clicks_prev

    total_kw_curr = sum(pos_buckets_curr.values())
    total_kw_prior = sum(pos_buckets_prior.values())

    kw_curr = {
        "top_1_3": pos_buckets_curr["top_1_3"],
        "top_4_10": pos_buckets_curr["top_4_10"],
        "top_11_20": pos_buckets_curr["top_11_20"],
        "top_21_50": pos_buckets_curr["top_21_50"],
        "top_51_100": pos_buckets_curr["top_51_100"],
        "serp_features": 0,
        "total": total_kw_curr
    }
    kw_prior = {
        "top_1_3": pos_buckets_prior["top_1_3"],
        "top_4_10": pos_buckets_prior["top_4_10"],
        "top_11_20": pos_buckets_prior["top_11_20"],
        "top_21_50": pos_buckets_prior["top_21_50"],
        "top_51_100": pos_buckets_prior["top_51_100"],
        "serp_features": 0,
        "total": total_kw_prior
    }

    brand_data = {
        "branded_traffic": {"prior": str(brand_traffic_prior), "current": str(brand_traffic_curr)},
        "non_branded_traffic": {"prior": str(non_brand_traffic_prior), "current": str(non_brand_traffic_curr)},
        "branded_keywords": {"prior": str(brand_keywords_prior), "current": str(brand_keywords_curr)},
        "non_branded_keywords": {"prior": str(non_brand_keywords_prior), "current": str(non_brand_keywords_curr)}
    }

    # Crucial Metrics defaults (strictly unpopulated if authority service is unavailable)
    crucial_metrics_data = {
        "domain_rating": {"prior": "-", "current": "-"},
        "domain_authority": {"prior": "-", "current": "-"},
        "page_authority": {"prior": "-", "current": "-"},
        "spam_score": {"prior": "-", "current": "-"},
        "authority_score": {"prior": "-", "current": "-"},
        "total_backlinks": {"prior": "-", "current": "-"},
        "new_backlinks": {"prior": "-", "current": "-"},
        "new_referring_domains": {"prior": "-", "current": "-"}
    }

    semrush_metrics = {
        "prior_period_label": periods.get("comparison_period_label") or periods.get("prior_period_short") or "Prior Period",
        "current_period_label": periods.get("primary_period_label") or periods.get("curr_period_short") or "Current Period",
        "footnote": "*Data taken from verified search telemetry",
        "organic_keywords": {
            "top_1_3": {"prior": kw_prior["top_1_3"], "current": kw_curr["top_1_3"]},
            "top_4_10": {"prior": kw_prior["top_4_10"], "current": kw_curr["top_4_10"]},
            "top_11_20": {"prior": kw_prior["top_11_20"], "current": kw_curr["top_11_20"]},
            "top_21_50": {"prior": kw_prior["top_21_50"], "current": kw_curr["top_21_50"]},
            "top_51_100": {"prior": kw_prior["top_51_100"], "current": kw_curr["top_51_100"]},
            "serp_features": {"prior": kw_prior["serp_features"], "current": kw_curr["serp_features"]},
            "total": {"prior": kw_prior["total"], "current": kw_curr["total"]}
        },
        "branded_non_branded": brand_data,
        "crucial_metrics": crucial_metrics_data
    }

    # 7. Off-Page Backlink Submissions (Page 12)
    # Authentic default: 0 across all categories unless manually logged or integrated
    offpage_metrics = {
        "period_label": periods.get("primary_period_label") or periods.get("curr_period_short") or "Current Period",
        "narrative": "Detailed backlinks counts conducted during the month.",
        "report_link_text": f"{comp_name} Backlinks Report",
        "report_link_url": "",
        "rows": {
            "business_listing": 0,
            "profile": 0,
            "blog": 0,
            "pdf": 0,
            "forum": 0,
            "quora": 0,
            "pinterest": 0,
            "image_infographic": 0,
            "total": 0
        }
    }

    return {
        "metadata": {
            "website": website,
            "domain": clean_domain,
            "company_name": comp_name,
            "report_month": periods["report_month"],
            "period_label": periods["period_label"],
            "primary_period_label": periods["primary_period_label"],
            "comparison_period_label": periods["comparison_period_label"],
            "curr_month_label": periods["curr_month_label"],
            "prior_month_label": periods["prior_month_label"],
            "curr_month_short": periods["curr_month_short"],
            "prior_month_short": periods["prior_month_short"],
            "primary_period_long": periods["primary_period_long"],
            "prior_period_long": periods["prior_period_long"],
            "curr_period_short": periods["curr_period_short"],
            "prior_period_short": periods["prior_period_short"],
            "curr_period_slash": periods["curr_period_slash"],
            "prior_period_slash": periods["prior_period_slash"],
            "curr_start_str": periods["curr_start_str"],
            "curr_end_str": periods["curr_end_str"],
            "prior_start_str": periods["prior_start_str"],
            "prior_end_str": periods["prior_end_str"],
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "is_sandbox": False
        },
        "section_01_website_traffic": section_01_website_traffic,
        "section_02_google_search": section_02_google_search,
        "section_03_keyword_visibility": section_03_keyword_visibility,
        "section_04_authority_backlinks": section_04_authority_backlinks,
        "section_05_technical_seo": section_05_technical_seo,
        "semrush_metrics": semrush_metrics,
        "offpage_metrics": offpage_metrics
    }
