import calendar
import asyncio
import urllib.parse
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
import httpx

GSC_API_BASE = "https://www.googleapis.com/webmasters/v3"

async def fetch_gsc_sites(access_token: str) -> List[Dict[str, Any]]:
    """
    Retrieves all verified sites and domain properties in user's Google Search Console.
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    url = f"{GSC_API_BASE}/sites"

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            error_msg = resp.text
            raise RuntimeError(f"GSC get sites failed (HTTP {resp.status_code}): {error_msg}")
        
        data = resp.json()
        site_entries = data.get("siteEntry", [])
        return [
            {
                "siteUrl": entry.get("siteUrl"),
                "permissionLevel": entry.get("permissionLevel")
            }
            for entry in site_entries
        ]

async def query_gsc_search_analytics(
    access_token: str,
    site_url: str,
    start_date: str,
    end_date: str,
    dimensions: Optional[List[str]] = None,
    row_limit: int = 50
) -> List[Dict[str, Any]]:
    """
    Queries Google Search Console Search Analytics API for specific dimensions.
    """
    encoded_site = urllib.parse.quote(site_url, safe="")
    url = f"{GSC_API_BASE}/sites/{encoded_site}/searchAnalytics/query"

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    payload: Dict[str, Any] = {
        "startDate": start_date,
        "endDate": end_date,
        "rowLimit": row_limit,
        "searchType": "web"
    }
    if dimensions:
        payload["dimensions"] = dimensions

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code != 200:
            raise RuntimeError(f"GSC query failed ({site_url}, HTTP {resp.status_code}): {resp.text}")
        
        data = resp.json()
        return data.get("rows", [])

def resolve_gsc_periods(
    days: int = 30,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    prior_start_date: Optional[str] = None,
    prior_end_date: Optional[str] = None
) -> Dict[str, Any]:
    """
    Resolves day-matched current and prior periods for Google Search Console.
    Respects GSC's 2-3 day data lag when date ranges are not explicitly provided.
    Supports explicit prior date ranges and calendar month alignment.
    """
    if start_date and end_date:
        cur_start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
        cur_end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()
        period_len = (cur_end_dt - cur_start_dt).days + 1

        if prior_start_date and prior_end_date:
            prior_start_dt = datetime.strptime(prior_start_date, "%Y-%m-%d").date()
            prior_end_dt = datetime.strptime(prior_end_date, "%Y-%m-%d").date()
        elif cur_start_dt.day == 1 and cur_start_dt.month == cur_end_dt.month and cur_start_dt.year == cur_end_dt.year:
            # Calendar month alignment
            if cur_start_dt.month == 1:
                p_year = cur_start_dt.year - 1
                p_month = 12
            else:
                p_year = cur_start_dt.year
                p_month = cur_start_dt.month - 1
            p_days_in_m = calendar.monthrange(p_year, p_month)[1]
            c_days_in_m = calendar.monthrange(cur_start_dt.year, cur_start_dt.month)[1]

            if cur_end_dt.day == c_days_in_m:
                # Full calendar month (e.g. Sep 1 - Sep 30 -> Aug 1 - Aug 31)
                prior_start_dt = cur_start_dt.replace(year=p_year, month=p_month, day=1)
                prior_end_dt = cur_start_dt.replace(year=p_year, month=p_month, day=p_days_in_m)
            else:
                prior_start_dt = cur_start_dt.replace(year=p_year, month=p_month, day=1)
                prior_end_dt = cur_start_dt.replace(year=p_year, month=p_month, day=min(cur_end_dt.day, p_days_in_m))
        else:
            prior_end_dt = cur_start_dt - timedelta(days=1)
            prior_start_dt = prior_end_dt - timedelta(days=period_len - 1)
    else:
        # GSC has 2-3 days data lag
        cur_end_dt = datetime.utcnow().date() - timedelta(days=2)
        cur_start_dt = cur_end_dt - timedelta(days=days - 1)
        period_len = days
        prior_end_dt = cur_start_dt - timedelta(days=1)
        prior_start_dt = prior_end_dt - timedelta(days=days - 1)

    c_start_str = cur_start_dt.strftime("%Y-%m-%d")
    c_end_str = cur_end_dt.strftime("%Y-%m-%d")
    p_start_str = prior_start_dt.strftime("%Y-%m-%d")
    p_end_str = prior_end_dt.strftime("%Y-%m-%d")

    curr_label = f"{cur_start_dt.day}–{cur_end_dt.day} {cur_end_dt.strftime('%b %Y')}"
    prior_label = f"{prior_start_dt.day}–{prior_end_dt.day} {prior_end_dt.strftime('%b %Y')}"
    curr_slash = f"{cur_start_dt.month}/{cur_start_dt.day}/{str(cur_start_dt.year)[2:]} - {cur_end_dt.month}/{cur_end_dt.day}/{str(cur_end_dt.year)[2:]}"
    prior_slash = f"{prior_start_dt.month}/{prior_start_dt.day}/{str(prior_start_dt.year)[2:]} - {prior_end_dt.month}/{prior_end_dt.day}/{str(prior_end_dt.year)[2:]}"

    return {
        "curr_start_str": c_start_str,
        "curr_end_str": c_end_str,
        "prior_start_str": p_start_str,
        "prior_end_str": p_end_str,
        "curr_label": curr_label,
        "prior_label": prior_label,
        "curr_slash": curr_slash,
        "prior_slash": prior_slash,
        "period_len": period_len
    }

async def fetch_gsc_full_dataset(
    access_token: str,
    site_url: str,
    days: int = 30,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    prior_start_date: Optional[str] = None,
    prior_end_date: Optional[str] = None
) -> Dict[str, Any]:
    """
    Fetches full authentic GSC analytics dataset with month-over-month comparisons:
    1. Dual-period trend over time (current clicks/impressions vs prior clicks/impressions)
    2. Page 8: Comparative Top queries (Clicks Prev, Curr, Diff & Impressions Prev, Curr, Diff)
    3. Page 8: Comparative Top landing pages (Clicks Prev, Curr, Diff & Impressions Prev, Curr, Diff)
    4. Page 7: Overview Summary metrics (Clicks, Impressions, CTR, Position with prior period values & MoM deltas)
    Strictly NO data fabrication.
    """
    periods = resolve_gsc_periods(
        days=days,
        start_date=start_date,
        end_date=end_date,
        prior_start_date=prior_start_date,
        prior_end_date=prior_end_date
    )
    start_date_str = periods["curr_start_str"]
    end_date_str = periods["curr_end_str"]
    p_start_str = periods["prior_start_str"]
    p_end_str = periods["prior_end_str"]
    curr_label = periods["curr_label"]
    prior_label = periods["prior_label"]
    curr_slash = periods["curr_slash"]
    prior_slash = periods["prior_slash"]

    # Pre-fetch all GSC queries concurrently via asyncio.gather
    t_cur_summary = query_gsc_search_analytics(access_token, site_url, start_date_str, end_date_str, dimensions=None)
    t_prior_summary = query_gsc_search_analytics(access_token, site_url, p_start_str, p_end_str, dimensions=None)
    t_cur_dates = query_gsc_search_analytics(access_token, site_url, start_date_str, end_date_str, dimensions=["date"], row_limit=days + 15)
    t_prior_dates = query_gsc_search_analytics(access_token, site_url, p_start_str, p_end_str, dimensions=["date"], row_limit=days + 15)
    t_cur_queries = query_gsc_search_analytics(access_token, site_url, start_date_str, end_date_str, dimensions=["query"], row_limit=35)
    t_prior_queries = query_gsc_search_analytics(access_token, site_url, p_start_str, p_end_str, dimensions=["query"], row_limit=50)
    t_cur_pages = query_gsc_search_analytics(access_token, site_url, start_date_str, end_date_str, dimensions=["page"], row_limit=25)
    t_prior_pages = query_gsc_search_analytics(access_token, site_url, p_start_str, p_end_str, dimensions=["page"], row_limit=40)

    raw_results = await asyncio.gather(
        t_cur_summary, t_prior_summary,
        t_cur_dates, t_prior_dates,
        t_cur_queries, t_prior_queries,
        t_cur_pages, t_prior_pages,
        return_exceptions=True
    )

    # If the current summary query failed (e.g. 401 unauthenticated), raise to trigger token refresh
    if isinstance(raw_results[0], Exception):
        raise raw_results[0]

    cur_summary_rows = raw_results[0] if isinstance(raw_results[0], list) else []
    prior_summary_rows = raw_results[1] if isinstance(raw_results[1], list) else []
    date_rows = raw_results[2] if isinstance(raw_results[2], list) else []
    prior_date_rows = raw_results[3] if isinstance(raw_results[3], list) else []
    cur_query_rows = raw_results[4] if isinstance(raw_results[4], list) else []
    prior_query_rows = raw_results[5] if isinstance(raw_results[5], list) else []
    cur_page_rows = raw_results[6] if isinstance(raw_results[6], list) else []
    prior_page_rows = raw_results[7] if isinstance(raw_results[7], list) else []

    sorted_cur_dates = sorted(date_rows, key=lambda r: r.get("keys", [""])[0])
    sorted_prior_dates = sorted(prior_date_rows, key=lambda r: r.get("keys", [""])[0])

    # 1. Timeline by date (spanning all days across both current and prior periods)
    max_days = max(len(sorted_cur_dates), len(sorted_prior_dates))
    trend = []
    daily_c_clicks = 0
    daily_c_imp = 0
    daily_c_weighted_pos = 0.0

    for i in range(max_days):
        cur_row = sorted_cur_dates[i] if i < len(sorted_cur_dates) else None
        prior_row = sorted_prior_dates[i] if i < len(sorted_prior_dates) else None

        if cur_row:
            date_raw = cur_row.get("keys", [""])[0]
            try:
                formatted_date = datetime.strptime(date_raw, "%Y-%m-%d").strftime("%b %d")
            except Exception:
                formatted_date = date_raw
        elif prior_row:
            date_raw = prior_row.get("keys", [""])[0]
            try:
                formatted_date = datetime.strptime(date_raw, "%Y-%m-%d").strftime("%b %d")
            except Exception:
                formatted_date = f"Day {i + 1}"
        else:
            formatted_date = f"Day {i + 1}"

        if cur_row:
            c = int(cur_row.get("clicks", 0))
            imp = int(cur_row.get("impressions", 0))
            ctr = round(float(cur_row.get("ctr", 0.0)) * 100, 2)
            pos = round(float(cur_row.get("position", 0.0)), 1)
            daily_c_clicks += c
            daily_c_imp += imp
            daily_c_weighted_pos += float(cur_row.get("position", 0.0)) * imp
        else:
            c = None
            imp = None
            ctr = None
            pos = None

        if prior_row:
            p_c = int(prior_row.get("clicks", 0))
            p_imp = int(prior_row.get("impressions", 0))
            p_ctr = round(float(prior_row.get("ctr", 0.0)) * 100, 2)
            p_pos = round(float(prior_row.get("position", 0.0)), 1)
        else:
            p_c = None
            p_imp = None
            p_ctr = None
            p_pos = None

        trend.append({
            "date": formatted_date,
            "clicks": c,
            "priorClicks": p_c,
            "impressions": imp,
            "priorImpressions": p_imp,
            "ctr": ctr,
            "priorCtr": p_ctr,
            "position": pos,
            "priorPosition": p_pos
        })

    # Summary metrics: use authentic GSC aggregate rows from dimensions=None query
    raw_cur_ctr = None
    if cur_summary_rows and len(cur_summary_rows) > 0:
        tot_clicks = int(cur_summary_rows[0].get("clicks", 0))
        tot_impressions = int(cur_summary_rows[0].get("impressions", 0))
        raw_cur_ctr = float(cur_summary_rows[0].get("ctr", 0.0))
        avg_ctr = round(raw_cur_ctr * 100, 2)
        avg_pos = round(float(cur_summary_rows[0].get("position", 0.0)), 1)
    else:
        tot_clicks = daily_c_clicks
        tot_impressions = daily_c_imp
        avg_ctr = round((tot_clicks / max(1, tot_impressions)) * 100, 2)
        raw_cur_ctr = (tot_clicks / max(1, tot_impressions))
        avg_pos = round(daily_c_weighted_pos / max(1, tot_impressions), 1) if tot_impressions > 0 else 0.0

    p_clicks = None
    p_impressions = None
    p_ctr = None
    p_pos = None
    raw_prior_ctr = None
    clicks_change = None
    impressions_change = None
    ctr_change = None
    position_change = None

    if prior_summary_rows and len(prior_summary_rows) > 0:
        p_clicks = int(prior_summary_rows[0].get("clicks", 0))
        p_impressions = int(prior_summary_rows[0].get("impressions", 0))
        raw_prior_ctr = float(prior_summary_rows[0].get("ctr", 0.0))
        p_ctr = round(raw_prior_ctr * 100, 2)
        p_pos = round(float(prior_summary_rows[0].get("position", 0.0)), 1)
    elif sorted_prior_dates:
        p_clicks = sum(int(r.get("clicks", 0)) for r in sorted_prior_dates)
        p_impressions = sum(int(r.get("impressions", 0)) for r in sorted_prior_dates)
        raw_prior_ctr = (p_clicks / max(1, p_impressions))
        p_ctr = round(raw_prior_ctr * 100, 2)
        sum_p_weighted = sum(float(r.get("position", 0.0)) * int(r.get("impressions", 0)) for r in sorted_prior_dates)
        p_pos = round(sum_p_weighted / max(1, p_impressions), 1) if p_impressions > 0 else 0.0

    if p_clicks is not None:
        if p_clicks > 0:
            clicks_change = round(((tot_clicks - p_clicks) / p_clicks) * 100.0, 1)
        elif tot_clicks == 0 and p_clicks == 0:
            clicks_change = 0.0
        else:
            clicks_change = None

    if p_impressions is not None:
        if p_impressions > 0:
            impressions_change = round(((tot_impressions - p_impressions) / p_impressions) * 100.0, 1)
        elif tot_impressions == 0 and p_impressions == 0:
            impressions_change = 0.0
        else:
            impressions_change = None

    if p_ctr is not None and avg_ctr is not None:
        ctr_change = round(avg_ctr - p_ctr, 2)

    if p_pos is not None:
        position_change = round(avg_pos - p_pos, 1)

    # 2. Page 8: Comparative Top search queries (Clicks Prev, Curr, Diff & Impressions Prev, Curr, Diff)
    keywords = []
    try:
        cur_query_rows = await query_gsc_search_analytics(
            access_token=access_token,
            site_url=site_url,
            start_date=start_date_str,
            end_date=end_date_str,
            dimensions=["query"],
            row_limit=35
        )
        prior_query_rows = await query_gsc_search_analytics(
            access_token=access_token,
            site_url=site_url,
            start_date=p_start_str,
            end_date=p_end_str,
            dimensions=["query"],
            row_limit=50
        )

        prior_query_map = {}
        for r in prior_query_rows:
            kw = r.get("keys", [""])[0]
            prior_query_map[kw] = {
                "clicks": int(r.get("clicks", 0)),
                "impressions": int(r.get("impressions", 0)),
                "ctr": round(float(r.get("ctr", 0.0)) * 100, 1),
                "position": round(float(r.get("position", 0.0)), 1)
            }

        for r in cur_query_rows:
            kw = r.get("keys", [""])[0]
            c_clicks = int(r.get("clicks", 0))
            c_imp = int(r.get("impressions", 0))
            c_ctr = round(float(r.get("ctr", 0.0)) * 100, 1)
            c_pos = round(float(r.get("position", 0.0)), 1)

            p_data = prior_query_map.get(kw, {"clicks": 0, "impressions": 0, "ctr": 0.0, "position": 0.0})
            p_clicks_val = p_data["clicks"]
            p_imp_val = p_data["impressions"]

            keywords.append({
                "keyword": kw,
                "query": kw,
                "clicks_prev": p_clicks_val,
                "clicks_curr": c_clicks,
                "clicks_diff": c_clicks - p_clicks_val,
                "impressions_prev": p_imp_val,
                "impressions_curr": c_imp,
                "impressions_diff": c_imp - p_imp_val,
                "clicks": c_clicks,
                "impressions": c_imp,
                "ctr": c_ctr,
                "position": c_pos,
                "position_prev": p_data.get("position", 0.0),
                "prev_position": p_data.get("position", 0.0)
            })
    except Exception as ex:
        print(f"Notice: GSC keywords extraction error: {ex}")

    # 3. Page 8: Comparative Top landing pages (Clicks Prev, Curr, Diff & Impressions Prev, Curr, Diff)
    pages = []
    try:
        cur_page_rows = await query_gsc_search_analytics(
            access_token=access_token,
            site_url=site_url,
            start_date=start_date_str,
            end_date=end_date_str,
            dimensions=["page"],
            row_limit=25
        )
        prior_page_rows = await query_gsc_search_analytics(
            access_token=access_token,
            site_url=site_url,
            start_date=p_start_str,
            end_date=p_end_str,
            dimensions=["page"],
            row_limit=40
        )

        prior_page_map = {}
        for r in prior_page_rows:
            pg = r.get("keys", [""])[0]
            prior_page_map[pg] = {
                "clicks": int(r.get("clicks", 0)),
                "impressions": int(r.get("impressions", 0)),
                "ctr": round(float(r.get("ctr", 0.0)) * 100, 1),
                "position": round(float(r.get("position", 0.0)), 1)
            }

        for r in cur_page_rows:
            pg = r.get("keys", [""])[0]
            c_clicks = int(r.get("clicks", 0))
            c_imp = int(r.get("impressions", 0))
            c_ctr = round(float(r.get("ctr", 0.0)) * 100, 1)
            c_pos = round(float(r.get("position", 0.0)), 1)

            p_data = prior_page_map.get(pg, {"clicks": 0, "impressions": 0, "ctr": 0.0, "position": 0.0})
            p_clicks_val = p_data["clicks"]
            p_imp_val = p_data["impressions"]

            pages.append({
                "page": pg,
                "clicks_prev": p_clicks_val,
                "clicks_curr": c_clicks,
                "clicks_diff": c_clicks - p_clicks_val,
                "impressions_prev": p_imp_val,
                "impressions_curr": c_imp,
                "impressions_diff": c_imp - p_imp_val,
                "clicks": c_clicks,
                "impressions": c_imp,
                "ctr": c_ctr,
                "position": c_pos
            })
    except Exception as ex:
        print(f"Notice: GSC pages extraction error: {ex}")

    has_gsc_data = bool(sorted_cur_dates and len(sorted_cur_dates) > 0)

    return {
        "summary": {
            "clicks": tot_clicks if has_gsc_data else None,
            "priorClicks": p_clicks,
            "clicksChange": clicks_change,
            "impressions": tot_impressions if has_gsc_data else None,
            "priorImpressions": p_impressions,
            "impressionsChange": impressions_change,
            "ctr": avg_ctr if has_gsc_data else None,
            "priorCtr": p_ctr,
            "ctrFormatted": f"{raw_cur_ctr * 100:.2f}%" if raw_cur_ctr is not None else None,
            "priorCtrFormatted": f"{raw_prior_ctr * 100:.2f}%" if raw_prior_ctr is not None else None,
            "ctrChange": ctr_change,
            "position": avg_pos if has_gsc_data else None,
            "priorPosition": p_pos,
            "positionChange": position_change
        },
        "trend": trend,
        "keywords": keywords,
        "pages": pages,
        "siteUrl": site_url,
        "periods": {
            "currPeriodShort": curr_label,
            "priorPeriodShort": prior_label,
            "currPeriodSlash": curr_slash,
            "priorPeriodSlash": prior_slash
        },
        "isRealData": True
    }
