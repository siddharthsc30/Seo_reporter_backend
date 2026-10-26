from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
import calendar
import asyncio
import re
import httpx

GA4_ADMIN_BASE = "https://analyticsadmin.googleapis.com/v1beta"
GA4_DATA_BASE = "https://analyticsdata.googleapis.com/v1beta"

async def fetch_ga4_properties(access_token: str) -> List[Dict[str, Any]]:
    """
    Lists all accessible GA4 properties across the user's Google Analytics accounts.
    Uses the Google Analytics Admin API accountSummaries endpoint with pagination.
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    url = f"{GA4_ADMIN_BASE}/accountSummaries"
    properties = []
    page_token = None

    async with httpx.AsyncClient(timeout=20.0) as client:
        while True:
            params = {"pageSize": 200}
            if page_token:
                params["pageToken"] = page_token

            resp = await client.get(url, headers=headers, params=params)
            if resp.status_code != 200:
                print(f"GA4 account summaries failed (HTTP {resp.status_code}): {resp.text}")
                break

            data = resp.json()
            account_summaries = data.get("accountSummaries", [])
            
            for acct in account_summaries:
                acct_name = acct.get("displayName", "Account")
                for prop in acct.get("propertySummaries", []):
                    prop_id = prop.get("property", "") # e.g. "properties/12345678"
                    prop_display = prop.get("displayName", "Unnamed Property")
                    properties.append({
                        "propertyId": prop_id,
                        "displayName": f"{prop_display} ({prop_id.replace('properties/', '')})",
                        "accountName": acct_name
                    })

            page_token = data.get("nextPageToken")
            if not page_token:
                break

    return properties

async def run_ga4_report(
    access_token: str,
    property_id: str,
    metrics: List[str],
    dimensions: Optional[List[str]] = None,
    days: int = 30,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None
) -> Dict[str, Any]:
    """
    Executes a runReport request against the Google Analytics Data API.
    """
    clean_prop_id = property_id if property_id.startswith("properties/") else f"properties/{property_id}"
    url = f"{GA4_DATA_BASE}/{clean_prop_id}:runReport"

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    start_date_str = start_date or f"{days}daysAgo"
    end_date_str = end_date or "today"

    payload: Dict[str, Any] = {
        "dateRanges": [{"startDate": start_date_str, "endDate": end_date_str}],
        "metrics": [{"name": m} for m in metrics]
    }
    if dimensions:
        payload["dimensions"] = [{"name": d} for d in dimensions]

    if client is not None:
        resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code != 200:
            raise RuntimeError(f"GA4 runReport failed ({clean_prop_id}, HTTP {resp.status_code}): {resp.text}")
        return resp.json()

    async with httpx.AsyncClient(timeout=20.0) as local_client:
        resp = await local_client.post(url, headers=headers, json=payload)
        if resp.status_code != 200:
            raise RuntimeError(f"GA4 runReport failed ({clean_prop_id}, HTTP {resp.status_code}): {resp.text}")
        return resp.json()

def resolve_ga4_periods(
    days: int = 30, 
    start_date: Optional[str] = None, 
    end_date: Optional[str] = None,
    prior_start_date: Optional[str] = None,
    prior_end_date: Optional[str] = None
) -> Dict[str, Any]:
    """
    Computes exact current and prior periods for GA4 MoM comparisons.
    Supports explicitly passed prior periods, full calendar month alignment (e.g. Sep 1 - Sep 30 vs Aug 1 - Aug 31),
    and exact day-shift for custom windows.
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
                # Full calendar month: e.g. Sep 1 - Sep 30 -> Aug 1 - Aug 31
                prior_start_dt = cur_start_dt.replace(year=p_year, month=p_month, day=1)
                prior_end_dt = cur_start_dt.replace(year=p_year, month=p_month, day=p_days_in_m)
            else:
                prior_start_dt = cur_start_dt.replace(year=p_year, month=p_month, day=1)
                prior_end_dt = cur_start_dt.replace(year=p_year, month=p_month, day=min(cur_end_dt.day, p_days_in_m))
        else:
            prior_end_dt = cur_start_dt - timedelta(days=1)
            prior_start_dt = prior_end_dt - timedelta(days=period_len - 1)
    else:
        cur_end_dt = datetime.utcnow().date()
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

async def fetch_ga4_channel_acquisition_dataset(
    access_token: str,
    property_id: str,
    start_date: str,
    end_date: str,
    client: httpx.AsyncClient
) -> Dict[str, Any]:
    """
    Fetches channel acquisition metrics:
    1. User Acquisition (firstUserPrimaryChannelGroup / firstUserDefaultChannelGroup)
       for authentic totalUsers, newUsers, and returningUsers (via newVsReturning breakdown).
    2. Traffic Acquisition (sessionPrimaryChannelGroup / sessionDefaultChannelGroup)
       for authentic sessions, engagedSessions, and engagementRate matching the GA4 Traffic Acquisition UI.
    """
    # 1. User-scoped attribution (totalUsers, newUsers)
    chan_dim = "firstUserPrimaryChannelGroup"
    try:
        chan_resp = await run_ga4_report(
            access_token,
            property_id,
            ["totalUsers", "newUsers"],
            dimensions=[chan_dim],
            start_date=start_date,
            end_date=end_date,
            client=client
        )
    except Exception as ex:
        print(f"Notice: GA4 firstUserPrimaryChannelGroup failed for {property_id}, falling back to firstUserDefaultChannelGroup: {ex}")
        chan_dim = "firstUserDefaultChannelGroup"
        chan_resp = await run_ga4_report(
            access_token,
            property_id,
            ["totalUsers", "newUsers"],
            dimensions=[chan_dim],
            start_date=start_date,
            end_date=end_date,
            client=client
        )

    # Returning users breakdown
    ret_map: Dict[str, int] = {}
    try:
        ret_resp = await run_ga4_report(
            access_token,
            property_id,
            ["activeUsers"],
            dimensions=[chan_dim, "newVsReturning"],
            start_date=start_date,
            end_date=end_date,
            client=client
        )
        for row in ret_resp.get("rows", []):
            dims = row.get("dimensionValues", [])
            if len(dims) >= 2:
                c_name = dims[0].get("value", "")
                nvr = dims[1].get("value", "").lower()
                if nvr == "returning":
                    mvals = row.get("metricValues", [])
                    if mvals:
                        ret_map[c_name] = int(mvals[0].get("value", 0))
    except Exception as ex:
        print(f"Notice: GA4 returningUsers breakdown extraction failed ({chan_dim}): {ex}")

    # 2. Session-scoped attribution (sessions, engagedSessions, engagementRate) matching Traffic Acquisition report
    sess_dim = "sessionPrimaryChannelGroup"
    try:
        sess_resp = await run_ga4_report(
            access_token,
            property_id,
            ["sessions", "engagedSessions", "engagementRate"],
            dimensions=[sess_dim],
            start_date=start_date,
            end_date=end_date,
            client=client
        )
    except Exception as ex:
        print(f"Notice: GA4 sessionPrimaryChannelGroup failed for {property_id}, falling back to sessionDefaultChannelGroup: {ex}")
        sess_dim = "sessionDefaultChannelGroup"
        sess_resp = await run_ga4_report(
            access_token,
            property_id,
            ["sessions", "engagedSessions", "engagementRate"],
            dimensions=[sess_dim],
            start_date=start_date,
            end_date=end_date,
            client=client
        )

    sess_map: Dict[str, Dict[str, Any]] = {}
    for row in sess_resp.get("rows", []):
        dims = row.get("dimensionValues", [])
        mvals = row.get("metricValues", [])
        if dims and len(mvals) >= 3:
            c_name = dims[0].get("value", "")
            s_val = int(mvals[0].get("value", 0))
            es_val = int(mvals[1].get("value", 0))
            er_val = round(float(mvals[2].get("value", 0.0)) * 100.0, 2)
            sess_map[c_name] = {
                "sessions": s_val,
                "engagedSessions": es_val,
                "engagementRate": er_val
            }

    return {
        "channel_resp": chan_resp,
        "returning_map": ret_map,
        "session_map": sess_map,
        "chan_dim": chan_dim,
        "sess_dim": sess_dim
    }

async def fetch_ga4_full_dataset(
    access_token: str,
    property_id: str,
    days: int = 30,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    prior_start_date: Optional[str] = None,
    prior_end_date: Optional[str] = None
) -> Dict[str, Any]:
    """
    Fetches full authentic GA4 analytics dataset with month-over-month comparisons:
    1. Summary KPIs (users, sessions, pageviews, bounceRate, avgSessionDuration)
    2. Daily traffic timeline (current vs prior periods)
    3. User Sessions Overview (Page 4 table format by channel: Prior vs Current)
    4. Geographical Insights (Page 5 table format by country: Prior vs Current)
    5. User Engagement & Performance (Page 6 format scorecards & event telemetry)
    6. Devices & summary breakdowns
    Strictly NO data fabrication.
    """
    periods = resolve_ga4_periods(
        days=days, 
        start_date=start_date, 
        end_date=end_date,
        prior_start_date=prior_start_date,
        prior_end_date=prior_end_date
    )
    c_start = periods["curr_start_str"]
    c_end = periods["curr_end_str"]
    p_start = periods["prior_start_str"]
    p_end = periods["prior_end_str"]
    curr_label = periods["curr_label"]
    prior_label = periods["prior_label"]

    # Pre-fetch all 11 GA4 report datasets in parallel using asyncio.gather
    async with httpx.AsyncClient(timeout=25.0) as client:
        t_summary = run_ga4_report(access_token, property_id, ["totalUsers", "sessions", "screenPageViews", "bounceRate", "averageSessionDuration"], start_date=c_start, end_date=c_end, client=client)
        t_prior = run_ga4_report(access_token, property_id, ["totalUsers", "sessions", "screenPageViews", "bounceRate", "averageSessionDuration"], start_date=p_start, end_date=p_end, client=client)
        t_cur_timeline = run_ga4_report(access_token, property_id, ["sessions", "activeUsers", "screenPageViews", "newUsers", "eventCount"], dimensions=["date"], start_date=c_start, end_date=c_end, client=client)
        t_prior_timeline = run_ga4_report(access_token, property_id, ["sessions", "activeUsers", "screenPageViews", "newUsers", "eventCount"], dimensions=["date"], start_date=p_start, end_date=p_end, client=client)
        t_cur_chan = fetch_ga4_channel_acquisition_dataset(access_token, property_id, c_start, c_end, client)
        t_prior_chan = fetch_ga4_channel_acquisition_dataset(access_token, property_id, p_start, p_end, client)
        t_cur_geo = run_ga4_report(access_token, property_id, ["activeUsers", "newUsers", "sessions", "engagedSessions"], dimensions=["country"], start_date=c_start, end_date=c_end, client=client)
        t_prior_geo = run_ga4_report(access_token, property_id, ["activeUsers", "newUsers", "sessions", "engagedSessions"], dimensions=["country"], start_date=p_start, end_date=p_end, client=client)
        t_cur_eng = run_ga4_report(access_token, property_id, ["eventCount", "keyEvents", "activeUsers", "newUsers", "sessions", "engagedSessions"], start_date=c_start, end_date=c_end, client=client)
        t_prior_eng = run_ga4_report(access_token, property_id, ["eventCount", "keyEvents", "activeUsers", "newUsers", "sessions", "engagedSessions"], start_date=p_start, end_date=p_end, client=client)
        t_device = run_ga4_report(access_token, property_id, ["sessions"], dimensions=["deviceCategory"], start_date=c_start, end_date=c_end, client=client)

        raw_results = await asyncio.gather(
            t_summary, t_prior,
            t_cur_timeline, t_prior_timeline,
            t_cur_chan, t_prior_chan,
            t_cur_geo, t_prior_geo,
            t_cur_eng, t_prior_eng,
            t_device,
            return_exceptions=True
        )

    # If the primary report query failed (e.g. HTTP 403 Forbidden or 401), raise to prevent silent false success
    if isinstance(raw_results[0], Exception):
        raise raw_results[0]

    summary_resp = raw_results[0] if isinstance(raw_results[0], dict) else {}
    prior_resp = raw_results[1] if isinstance(raw_results[1], dict) else {}
    cur_timeline_resp = raw_results[2] if isinstance(raw_results[2], dict) else {}
    prior_timeline_resp = raw_results[3] if isinstance(raw_results[3], dict) else {}
    cur_chan_data = raw_results[4] if isinstance(raw_results[4], dict) else {}
    prior_chan_data = raw_results[5] if isinstance(raw_results[5], dict) else {}

    cur_chan_resp = cur_chan_data.get("channel_resp", {}) if isinstance(cur_chan_data, dict) else {}
    cur_ret_map = cur_chan_data.get("returning_map", {}) if isinstance(cur_chan_data, dict) else {}
    cur_sess_map = cur_chan_data.get("session_map", {}) if isinstance(cur_chan_data, dict) else {}

    prior_chan_resp = prior_chan_data.get("channel_resp", {}) if isinstance(prior_chan_data, dict) else {}
    prior_ret_map = prior_chan_data.get("returning_map", {}) if isinstance(prior_chan_data, dict) else {}
    prior_sess_map = prior_chan_data.get("session_map", {}) if isinstance(prior_chan_data, dict) else {}

    cur_geo_resp = raw_results[6] if isinstance(raw_results[6], dict) else {}
    prior_geo_resp = raw_results[7] if isinstance(raw_results[7], dict) else {}
    cur_eng_resp = raw_results[8] if isinstance(raw_results[8], dict) else {}
    prior_eng_resp = raw_results[9] if isinstance(raw_results[9], dict) else {}
    device_resp = raw_results[10] if isinstance(raw_results[10], dict) else {}

    tot_users = 0
    tot_sessions = 0
    tot_pageviews = 0
    bounce_rate = None
    avg_duration_secs = None
    avg_session_duration_formatted = None

    if summary_resp.get("rows") and len(summary_resp["rows"]) > 0:
        metric_values = summary_resp["rows"][0].get("metricValues", [])
        if len(metric_values) >= 5:
            tot_users = int(metric_values[0].get("value", 0))
            tot_sessions = int(metric_values[1].get("value", 0))
            tot_pageviews = int(metric_values[2].get("value", 0))
            bounce_rate = round(float(metric_values[3].get("value", 0.0)) * 100, 1)
            avg_duration_secs = float(metric_values[4].get("value", 0.0))
            duration_mins = int(avg_duration_secs // 60)
            duration_rem_secs = int(avg_duration_secs % 60)
            avg_session_duration_formatted = f"{duration_mins}m {duration_rem_secs}s"

    # Compute prior period summary
    users_change = None
    sessions_change = None
    pageviews_change = None
    bounce_rate_change = None
    avg_duration_change = None

    try:
        if prior_resp.get("rows") and len(prior_resp["rows"]) > 0:
            p_mvals = prior_resp["rows"][0].get("metricValues", [])
            if len(p_mvals) >= 5:
                p_users = int(p_mvals[0].get("value", 0))
                p_sessions = int(p_mvals[1].get("value", 0))
                p_pageviews = int(p_mvals[2].get("value", 0))
                p_bounce = round(float(p_mvals[3].get("value", 0.0)) * 100, 1)
                p_dur = float(p_mvals[4].get("value", 0.0))

                if p_users > 0:
                    users_change = round(((tot_users - p_users) / p_users) * 100.0, 1)
                elif tot_users == 0 and p_users == 0:
                    users_change = 0.0
                else:
                    users_change = None

                if p_sessions > 0:
                    sessions_change = round(((tot_sessions - p_sessions) / p_sessions) * 100.0, 1)
                elif tot_sessions == 0 and p_sessions == 0:
                    sessions_change = 0.0
                else:
                    sessions_change = None

                if p_pageviews > 0:
                    pageviews_change = round(((tot_pageviews - p_pageviews) / p_pageviews) * 100.0, 1)
                elif tot_pageviews == 0 and p_pageviews == 0:
                    pageviews_change = 0.0
                else:
                    pageviews_change = None

                if bounce_rate is not None:
                    bounce_rate_change = round(bounce_rate - p_bounce, 1)

                if avg_duration_secs is not None and p_dur > 0:
                    avg_duration_change = round(((avg_duration_secs - p_dur) / p_dur) * 100.0, 1)
    except Exception:
        pass

    # 2. Daily Timeline: Current & Prior (Dual-line trend)
    traffic_over_time = []
    try:

        cur_rows = sorted(cur_timeline_resp.get("rows", []), key=lambda r: r.get("dimensionValues", [{}])[0].get("value", ""))
        prior_rows = sorted(prior_timeline_resp.get("rows", []), key=lambda r: r.get("dimensionValues", [{}])[0].get("value", ""))

        for i, row in enumerate(cur_rows):
            date_raw = row.get("dimensionValues", [{}])[0].get("value", "")
            try:
                d_obj = datetime.strptime(date_raw, "%Y%m%d")
                formatted_date = d_obj.strftime("%b %d")
            except Exception:
                formatted_date = date_raw

            mvals = row.get("metricValues", [])
            s = int(mvals[0].get("value", 0)) if len(mvals) > 0 else 0
            u = int(mvals[1].get("value", 0)) if len(mvals) > 1 else 0
            p = int(mvals[2].get("value", 0)) if len(mvals) > 2 else 0
            nu = int(mvals[3].get("value", 0)) if len(mvals) > 3 else 0
            ec = int(mvals[4].get("value", 0)) if len(mvals) > 4 else 0

            prior_s = 0
            prior_u = 0
            prior_p = 0
            prior_nu = 0
            prior_ec = 0
            if i < len(prior_rows):
                p_mvals = prior_rows[i].get("metricValues", [])
                prior_s = int(p_mvals[0].get("value", 0)) if len(p_mvals) > 0 else 0
                prior_u = int(p_mvals[1].get("value", 0)) if len(p_mvals) > 1 else 0
                prior_p = int(p_mvals[2].get("value", 0)) if len(p_mvals) > 2 else 0
                prior_nu = int(p_mvals[3].get("value", 0)) if len(p_mvals) > 3 else 0
                prior_ec = int(p_mvals[4].get("value", 0)) if len(p_mvals) > 4 else 0

            traffic_over_time.append({
                "date": formatted_date,
                "sessions": s,
                "users": u,
                "pageviews": p,
                "newUsers": nu,
                "eventCount": ec,
                "eventsPerSession": round(ec / max(1, s), 1) if s > 0 else 0.0,
                "priorSessions": prior_s,
                "priorUsers": prior_u,
                "priorPageviews": prior_p,
                "priorNewUsers": prior_nu,
                "priorEventCount": prior_ec,
                "priorEventsPerSession": round(prior_ec / max(1, prior_s), 1) if prior_s > 0 else 0.0
            })
    except Exception as ex:
        print(f"Notice: GA4 timeline extraction error: {ex}")

    # 3. Page 4: User Sessions Overview (Channel comparison table: 2 rows per channel)
    user_sessions_overview = []
    channels = []
    try:

        def is_excluded_channel(name: Optional[str]) -> bool:
            if not name:
                return False
            clean = re.sub(r'[-_\s]+', ' ', str(name).strip().lower())
            return clean in {"ai assistant", "cross network", "paid search", "display"} or clean.startswith("display ")

        prior_ch_map: Dict[str, Dict[str, Any]] = {}
        for r in prior_chan_resp.get("rows", []):
            ch_name = r.get("dimensionValues", [{}])[0].get("value", "Other")
            if is_excluded_channel(ch_name):
                continue
            mvals = r.get("metricValues", [])
            tu = int(mvals[0].get("value", 0)) if len(mvals) > 0 else 0
            nu = int(mvals[1].get("value", 0)) if len(mvals) > 1 else 0
            ru = prior_ret_map[ch_name] if ch_name in prior_ret_map else (max(0, tu - nu) if not prior_ret_map else 0)

            if ch_name in prior_sess_map:
                s_info = prior_sess_map[ch_name]
                sess = s_info["sessions"]
                es = s_info["engagedSessions"]
                er = s_info["engagementRate"]
            else:
                sess = int(mvals[2].get("value", 0)) if len(mvals) > 2 else 0
                es = int(mvals[3].get("value", 0)) if len(mvals) > 3 else 0
                er = round((es / max(1, sess)) * 100.0, 2)

            prior_ch_map[ch_name] = {
                "periodLabel": prior_label,
                "totalUsers": tu,
                "newUsers": nu,
                "returningUsers": ru,
                "sessions": sess,
                "engagedSessions": es,
                "engagementRate": er
            }

        for ch_name, s_info in prior_sess_map.items():
            if is_excluded_channel(ch_name):
                continue
            if ch_name not in prior_ch_map:
                prior_ch_map[ch_name] = {
                    "periodLabel": prior_label,
                    "totalUsers": 0,
                    "newUsers": 0,
                    "returningUsers": 0,
                    "sessions": s_info["sessions"],
                    "engagedSessions": s_info["engagedSessions"],
                    "engagementRate": s_info["engagementRate"]
                }

        cur_channels = []
        cur_ch_map: Dict[str, Dict[str, Any]] = {}
        for r in cur_chan_resp.get("rows", []):
            ch_name = r.get("dimensionValues", [{}])[0].get("value", "Other")
            if is_excluded_channel(ch_name):
                continue
            mvals = r.get("metricValues", [])
            tu = int(mvals[0].get("value", 0)) if len(mvals) > 0 else 0
            nu = int(mvals[1].get("value", 0)) if len(mvals) > 1 else 0
            ru = cur_ret_map[ch_name] if ch_name in cur_ret_map else (max(0, tu - nu) if not cur_ret_map else 0)

            if ch_name in cur_sess_map:
                s_info = cur_sess_map[ch_name]
                sess = s_info["sessions"]
                es = s_info["engagedSessions"]
                er = s_info["engagementRate"]
            else:
                sess = int(mvals[2].get("value", 0)) if len(mvals) > 2 else 0
                es = int(mvals[3].get("value", 0)) if len(mvals) > 3 else 0
                er = round((es / max(1, sess)) * 100.0, 2)

            cur_channels.append(ch_name)
            cur_ch_map[ch_name] = {
                "periodLabel": curr_label,
                "totalUsers": tu,
                "newUsers": nu,
                "returningUsers": ru,
                "sessions": sess,
                "engagedSessions": es,
                "engagementRate": er
            }

        for ch_name, s_info in cur_sess_map.items():
            if is_excluded_channel(ch_name):
                continue
            if ch_name not in cur_ch_map:
                cur_channels.append(ch_name)
                cur_ch_map[ch_name] = {
                    "periodLabel": curr_label,
                    "totalUsers": 0,
                    "newUsers": 0,
                    "returningUsers": 0,
                    "sessions": s_info["sessions"],
                    "engagedSessions": s_info["engagedSessions"],
                    "engagementRate": s_info["engagementRate"]
                }

        for ch_name in cur_channels:
            c_data = cur_ch_map[ch_name]
            p_data = prior_ch_map.get(ch_name, {
                "periodLabel": prior_label,
                "totalUsers": 0,
                "newUsers": 0,
                "returningUsers": 0,
                "sessions": 0,
                "engagedSessions": 0,
                "engagementRate": 0.0
            })
            user_sessions_overview.append({
                "channel": ch_name,
                "prior": p_data,
                "current": c_data
            })

        # Add channels present only in prior period
        for ch_name, p_data in prior_ch_map.items():
            if ch_name not in cur_channels:
                user_sessions_overview.append({
                    "channel": ch_name,
                    "prior": p_data,
                    "current": {
                        "periodLabel": curr_label,
                        "totalUsers": 0,
                        "newUsers": 0,
                        "returningUsers": 0,
                        "sessions": 0,
                        "engagedSessions": 0,
                        "engagementRate": 0.0
                    }
                })

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

        merged_uso = []
        combined_organic = None
        for ch in user_sessions_overview:
            c_name = ch.get("channel", "")
            if is_organic_search_or_social(c_name):
                if not combined_organic:
                    combined_organic = {
                        "channel": "Organic (Search + Social)",
                        "prior": dict(ch.get("prior", {})),
                        "current": dict(ch.get("current", {}))
                    }
                    merged_uso.append(combined_organic)
                else:
                    p1 = combined_organic["prior"]
                    p2 = ch.get("prior", {})
                    p_tot = p1.get("totalUsers", 0) + p2.get("totalUsers", 0)
                    p_new = p1.get("newUsers", 0) + p2.get("newUsers", 0)
                    p_ret = p1.get("returningUsers", 0) + p2.get("returningUsers", 0)
                    p_sess = p1.get("sessions", 0) + p2.get("sessions", 0)
                    p_eng = p1.get("engagedSessions", 0) + p2.get("engagedSessions", 0)
                    p_rate = round((p_eng / max(1, p_sess)) * 100, 2)
                    combined_organic["prior"] = {
                        "periodLabel": p1.get("periodLabel") or p2.get("periodLabel") or "",
                        "totalUsers": p_tot,
                        "newUsers": p_new,
                        "returningUsers": p_ret,
                        "sessions": p_sess,
                        "engagedSessions": p_eng,
                        "engagementRate": p_rate
                    }

                    c1 = combined_organic["current"]
                    c2 = ch.get("current", {})
                    c_tot = c1.get("totalUsers", 0) + c2.get("totalUsers", 0)
                    c_new = c1.get("newUsers", 0) + c2.get("newUsers", 0)
                    c_ret = c1.get("returningUsers", 0) + c2.get("returningUsers", 0)
                    c_sess = c1.get("sessions", 0) + c2.get("sessions", 0)
                    c_eng = c1.get("engagedSessions", 0) + c2.get("engagedSessions", 0)
                    c_rate = round((c_eng / max(1, c_sess)) * 100, 2)
                    combined_organic["current"] = {
                        "periodLabel": c1.get("periodLabel") or c2.get("periodLabel") or "",
                        "totalUsers": c_tot,
                        "newUsers": c_new,
                        "returningUsers": c_ret,
                        "sessions": c_sess,
                        "engagedSessions": c_eng,
                        "engagementRate": c_rate
                    }
            else:
                merged_uso.append(ch)

        user_sessions_overview = merged_uso

        # Sort channels putting Organic (Search + Social) first, then by sessions descending
        user_sessions_overview.sort(
            key=lambda x: (not is_organic_search_or_social(x.get("channel", "")), -x["current"]["sessions"])
        )

        # Generate summary channel distribution for cards / pie chart
        channel_colors = {
            "Organic (Search + Social)": "#6366f1",
            "Organic Search": "#6366f1",
            "Direct": "#3b82f6",
            "Referral": "#10b981",
            "Social": "#f59e0b",
            "Organic Social": "#f59e0b",
            "Paid Search": "#ec4899",
            "Email": "#8b5cf6"
        }
        total_ch_sessions = sum(ch["current"]["sessions"] for ch in user_sessions_overview) or 1
        for idx, ch in enumerate(user_sessions_overview):
            c_name = ch["channel"]
            c_sess = ch["current"]["sessions"]
            pct = round((c_sess / total_ch_sessions) * 100)
            channels.append({
                "name": c_name,
                "value": pct,
                "sessions": c_sess,
                "color": channel_colors.get(c_name, f"hsl({(idx * 65) % 360}, 75%, 55%)")
            })
    except Exception as ex:
        print(f"Notice: GA4 userSessionsOverview extraction error: {ex}")

    # 4. Page 5: Geographical Insights (Country comparison table: 2 rows per country)
    geographical_insights = []
    countries = []
    try:

        prior_geo_map: Dict[str, Dict[str, Any]] = {}
        for r in prior_geo_resp.get("rows", []):
            ctry_name = r.get("dimensionValues", [{}])[0].get("value", "Global")
            mvals = r.get("metricValues", [])
            au = int(mvals[0].get("value", 0)) if len(mvals) > 0 else 0
            nu = int(mvals[1].get("value", 0)) if len(mvals) > 1 else 0
            sess = int(mvals[2].get("value", 0)) if len(mvals) > 2 else 0
            es = int(mvals[3].get("value", 0)) if len(mvals) > 3 else 0
            er = round((es / max(1, sess)) * 100.0, 2)
            prior_geo_map[ctry_name] = {
                "periodLabel": prior_label,
                "activeUsers": au,
                "newUsers": nu,
                "engagedSessions": es,
                "sessions": sess,
                "engagementRate": er
            }

        cur_countries_seen = []
        for r in cur_geo_resp.get("rows", []):
            ctry_name = r.get("dimensionValues", [{}])[0].get("value", "Global")
            mvals = r.get("metricValues", [])
            au = int(mvals[0].get("value", 0)) if len(mvals) > 0 else 0
            nu = int(mvals[1].get("value", 0)) if len(mvals) > 1 else 0
            sess = int(mvals[2].get("value", 0)) if len(mvals) > 2 else 0
            es = int(mvals[3].get("value", 0)) if len(mvals) > 3 else 0
            er = round((es / max(1, sess)) * 100.0, 2)

            cur_countries_seen.append(ctry_name)
            p_data = prior_geo_map.get(ctry_name, {
                "periodLabel": prior_label,
                "activeUsers": 0,
                "newUsers": 0,
                "engagedSessions": 0,
                "sessions": 0,
                "engagementRate": 0.0
            })

            geographical_insights.append({
                "country": ctry_name,
                "prior": p_data,
                "current": {
                    "periodLabel": curr_label,
                    "activeUsers": au,
                    "newUsers": nu,
                    "engagedSessions": es,
                    "sessions": sess,
                    "engagementRate": er
                }
            })

        for ctry_name, p_data in prior_geo_map.items():
            if ctry_name not in cur_countries_seen:
                geographical_insights.append({
                    "country": ctry_name,
                    "prior": p_data,
                    "current": {
                        "periodLabel": curr_label,
                        "activeUsers": 0,
                        "newUsers": 0,
                        "engagedSessions": 0,
                        "sessions": 0,
                        "engagementRate": 0.0
                    }
                })

        geographical_insights.sort(key=lambda x: x["current"]["activeUsers"], reverse=True)

        tot_geo_users = sum(g["current"]["activeUsers"] for g in geographical_insights) or 1
        for g in geographical_insights[:8]:
            c_name = g["country"]
            c_users = g["current"]["activeUsers"]
            countries.append({
                "name": c_name,
                "country": c_name,
                "value": round((c_users / tot_geo_users) * 100),
                "sessions": g["current"]["sessions"],
                "activeUsers": c_users
            })
    except Exception as ex:
        print(f"Notice: GA4 geographicalInsights extraction error: {ex}")

    # 5. Page 6: User Engagement and Performance (Key events, event count, new users, engaged sessions per user)
    user_engagement = {
        "eventCount": None,
        "eventCountChange": None,
        "keyEvents": None,
        "keyEventsChange": None,
        "newUsers": None,
        "newUsersChange": None,
        "engagedSessionsPerUser": None,
        "engagedSessionsPerUserChange": None,
        "engagementRate": None,
        "engagementRateChange": None
    }
    try:

        if cur_eng_resp.get("rows"):
            c_mvals = cur_eng_resp["rows"][0].get("metricValues", [])
            c_events = int(c_mvals[0].get("value", 0)) if len(c_mvals) > 0 else 0
            c_key_events = int(c_mvals[1].get("value", 0)) if len(c_mvals) > 1 else 0
            c_act_users = int(c_mvals[2].get("value", 0)) if len(c_mvals) > 2 else 0
            c_new_users = int(c_mvals[3].get("value", 0)) if len(c_mvals) > 3 else 0
            c_sess = int(c_mvals[4].get("value", 0)) if len(c_mvals) > 4 else 0
            c_eng_sess = int(c_mvals[5].get("value", 0)) if len(c_mvals) > 5 else 0

            c_eng_per_user = round(c_eng_sess / max(1, c_act_users), 2)
            c_eng_rate = round((c_eng_sess / max(1, c_sess)) * 100.0, 2)
            c_eps = round(c_events / max(1, c_sess), 1) if c_sess > 0 else 0.0

            user_engagement["eventCount"] = c_events
            user_engagement["keyEvents"] = c_key_events
            user_engagement["newUsers"] = c_new_users
            user_engagement["activeUsers"] = c_act_users
            user_engagement["eventsPerSession"] = c_eps
            user_engagement["engagedSessionsPerUser"] = c_eng_per_user
            user_engagement["engagementRate"] = c_eng_rate

            if prior_eng_resp.get("rows"):
                p_mvals = prior_eng_resp["rows"][0].get("metricValues", [])
                p_events = int(p_mvals[0].get("value", 0)) if len(p_mvals) > 0 else 0
                p_key_events = int(p_mvals[1].get("value", 0)) if len(p_mvals) > 1 else 0
                p_act_users = int(p_mvals[2].get("value", 0)) if len(p_mvals) > 2 else 0
                p_new_users = int(p_mvals[3].get("value", 0)) if len(p_mvals) > 3 else 0
                p_sess = int(p_mvals[4].get("value", 0)) if len(p_mvals) > 4 else 0
                p_eng_sess = int(p_mvals[5].get("value", 0)) if len(p_mvals) > 5 else 0

                p_eng_per_user = round(p_eng_sess / max(1, p_act_users), 2)
                p_eng_rate = round((p_eng_sess / max(1, p_sess)) * 100.0, 2)
                p_eps = round(p_events / max(1, p_sess), 1) if p_sess > 0 else 0.0

                user_engagement["priorEventsPerSession"] = p_eps
                user_engagement["priorActiveUsers"] = p_act_users
                user_engagement["priorNewUsers"] = p_new_users

                if p_eps > 0:
                    user_engagement["eventsPerSessionChange"] = round(((c_eps - p_eps) / p_eps) * 100.0, 1)
                elif c_eps == 0 and p_eps == 0:
                    user_engagement["eventsPerSessionChange"] = 0.0
                else:
                    user_engagement["eventsPerSessionChange"] = None

                if p_events > 0:
                    user_engagement["eventCountChange"] = round(((c_events - p_events) / p_events) * 100.0, 1)
                elif c_events == 0 and p_events == 0:
                    user_engagement["eventCountChange"] = 0.0
                else:
                    user_engagement["eventCountChange"] = None

                if p_key_events > 0:
                    user_engagement["keyEventsChange"] = round(((c_key_events - p_key_events) / p_key_events) * 100.0, 1)
                elif c_key_events == 0 and p_key_events == 0:
                    user_engagement["keyEventsChange"] = 0.0
                else:
                    user_engagement["keyEventsChange"] = None

                if p_new_users > 0:
                    user_engagement["newUsersChange"] = round(((c_new_users - p_new_users) / p_new_users) * 100.0, 1)
                elif c_new_users == 0 and p_new_users == 0:
                    user_engagement["newUsersChange"] = 0.0
                else:
                    user_engagement["newUsersChange"] = None

                if p_eng_per_user > 0:
                    user_engagement["engagedSessionsPerUserChange"] = round(((c_eng_per_user - p_eng_per_user) / p_eng_per_user) * 100.0, 1)
                elif c_eng_per_user == 0 and p_eng_per_user == 0:
                    user_engagement["engagedSessionsPerUserChange"] = 0.0
                else:
                    user_engagement["engagedSessionsPerUserChange"] = None

                if p_eng_rate is not None and c_eng_rate is not None:
                    user_engagement["engagementRateChange"] = round(c_eng_rate - p_eng_rate, 2)
                else:
                    user_engagement["engagementRateChange"] = None
    except Exception as ex:
        print(f"Notice: GA4 userEngagement extraction error: {ex}")

    # 6. Device Categories
    device_colors = {"desktop": "#6366f1", "mobile": "#10b981", "tablet": "#f59e0b"}
    devices = []
    try:
        raw_devices = device_resp.get("rows", [])
        total_dev_sessions = sum(int(r.get("metricValues", [{}])[0].get("value", 0)) for r in raw_devices) or 1
        for r in raw_devices:
            dev_name = r.get("dimensionValues", [{}])[0].get("value", "Desktop").capitalize()
            dev_sess = int(r.get("metricValues", [{}])[0].get("value", 0))
            pct = round((dev_sess / total_dev_sessions) * 100)
            devices.append({
                "name": dev_name,
                "value": pct,
                "color": device_colors.get(dev_name.lower(), "#6366f1")
            })
    except Exception:
        pass

    return {
        "summary": {
            "users": tot_users if summary_resp.get("rows") else None,
            "usersChange": users_change,
            "sessions": tot_sessions if summary_resp.get("rows") else None,
            "sessionsChange": sessions_change,
            "pageviews": tot_pageviews if summary_resp.get("rows") else None,
            "pageviewsChange": pageviews_change,
            "bounceRate": bounce_rate,
            "bounceRateChange": bounce_rate_change,
            "engagementRate": user_engagement.get("engagementRate") if user_engagement.get("engagementRate") is not None else (round(100.0 - bounce_rate, 2) if bounce_rate is not None else None),
            "engagementRateChange": user_engagement.get("engagementRateChange") if user_engagement.get("engagementRateChange") is not None else (round(-bounce_rate_change, 2) if bounce_rate_change is not None else None),
            "avgSessionDuration": avg_session_duration_formatted,
            "avgSessionDurationChange": avg_duration_change
        },
        "trafficOverTime": traffic_over_time,
        "userSessionsOverview": user_sessions_overview,
        "geographicalInsights": geographical_insights,
        "userEngagement": user_engagement,
        "channels": channels,
        "devices": devices,
        "countries": countries,
        "periods": {
            "currPeriodShort": curr_label,
            "priorPeriodShort": prior_label,
            "currPeriodSlash": periods["curr_slash"],
            "priorPeriodSlash": periods["prior_slash"]
        },
        "propertyId": property_id,
        "isRealData": True
    }
