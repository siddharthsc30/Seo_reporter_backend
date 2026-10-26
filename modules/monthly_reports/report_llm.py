import os
import json
from typing import Dict, Any, Optional, List
import httpx
from config import settings

SYSTEM_PROMPT = """You are the Lead SEO Analyst at SEO Reporter, an enterprise organic search optimization and performance intelligence platform.
You are synthesizing an executive monthly SEO report for an agency client based EXCLUSIVELY on authentic data collected from Google Analytics 4, Google Search Console, and SEMrush.

STRICT FACTUAL ACCURACY & ZERO-HALLUCINATION RULES (CRITICAL):
1. ZERO HALLUCINATED NUMBERS: You must ONLY cite exact numbers, counts, and percentages that appear in the provided input JSON. Never invent stats, traffic numbers, impressions, clicks, or ranks.
2. ZERO FABRICATION OF DELIVERABLES OR METRICS: If a metric, section, query, keyword, or backlink list in the input is missing, empty, or null, output "No data available" for that narrative and empty lists [] for lists. Do NOT invent fictional completed work, action items, or strategies.
3. NO PROMISSORY LANGUAGE: Never promise future outcomes (e.g. do NOT say "will guarantee #1 rankings" or "will double revenue"). Use measured, professional, analytical language.
4. PLAIN LANGUAGE DELTAS: Express Month-over-Month (MoM) performance clearly using plain deltas (e.g. "+14.2% MoM", "-1.2 pos improvement") ONLY if both current and prior data exist in the input. If prior data is not available, state the current metric factually without inventing a delta.
5. ALWAYS PRESERVE SOURCE ATTRIBUTION:
   - When discussing website traffic, sessions, users, bounce rate, or engagement rate, cite Google Analytics 4 (GA4).
   - When discussing search clicks, impressions, CTR, or average ranking position, cite Google Search Console (GSC).
   - When discussing keyword rankings and distribution, cite SEMrush.
6. STRICTLY GROUNDED RECOMMENDATIONS: For "upcoming_seo_focus", every focus bullet point MUST be directly linked to real opportunities or weaknesses identified in the data:
   - Queries with high impressions but low CTR -> recommend title tag / meta description / snippet optimization.
   - Queries or keywords ranking in positions 4-20 -> recommend content refreshes and internal linking to push to top 3 or top 10.
   - Landing pages with traffic changes or high bounce rate -> recommend UX and landing page optimization.
7. TABLES ARE IMMUTABLE: You are only generating narrative analysis, bullet points, and summaries. All structured tables remain pure data from the platforms.

Return ONLY a valid JSON object matching the requested schema.
"""

def generate_fallback_narratives(metrics_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deterministic rule-based narrative engine that triggers when OpenAI API
    is unconfigured, times out, or fails. Synthesizes factual executive commentary
    directly from verified input metrics.
    """
    meta = metrics_data.get("metadata", {})
    comp_name = meta.get("company_name", "Client")
    curr_month = meta.get("curr_month_label", "the current month")
    prior_month = meta.get("prior_month_label", "the previous month")

    traffic = metrics_data.get("section_01_website_traffic", {})
    t_strip = traffic.get("stat_strip", [])
    has_traffic = bool(t_strip and any(s.get("value") for s in t_strip))

    gsc = metrics_data.get("section_02_google_search", {})
    g_strip = gsc.get("stat_strip", [])
    has_gsc = bool(g_strip and any(s.get("value") for s in g_strip))

    kw = metrics_data.get("section_03_keyword_visibility", {})
    kw_strip = kw.get("stat_strip", [])
    has_kw = bool(kw_strip and any(s.get("value") for s in kw_strip))

    auth = metrics_data.get("section_04_authority_backlinks", {})
    auth_score = auth.get("authority_score")
    has_auth = auth_score is not None

    tech = metrics_data.get("section_05_technical_seo", {})
    tech_score = tech.get("overall_health_score")
    has_tech = tech_score is not None

    def _find_stat(strip: list, default_idx: int, *label_keywords) -> dict:
        for item in strip:
            lbl = item.get("label", "").lower()
            if any(k in lbl for k in label_keywords):
                return item
        if 0 <= default_idx < len(strip):
            return strip[default_idx]
        return {}

    # GA4 Metrics extraction
    u_obj = _find_stat(t_strip, 0, "user")
    users_val = u_obj.get("value", "")
    users_delta = u_obj.get("mom_delta", "") or u_obj.get("delta", "")

    s_obj = _find_stat(t_strip, 1, "session")
    sess_val = s_obj.get("value", "")
    sess_delta = s_obj.get("mom_delta", "") or s_obj.get("delta", "")

    d_obj = _find_stat(t_strip, 2, "duration", "engagement time", "avg time", "time")
    duration_val = d_obj.get("value", "")

    e_obj = _find_stat(t_strip, 3, "engagement rate", "bounce")
    eng_val = e_obj.get("value", "")

    # GSC Metrics extraction
    c_obj = _find_stat(g_strip, 0, "click")
    clicks_val = c_obj.get("value", "")
    clicks_delta = c_obj.get("mom_delta", "") or c_obj.get("delta", "")

    i_obj = _find_stat(g_strip, 1, "impression")
    imp_val = i_obj.get("value", "")
    imp_delta = i_obj.get("mom_delta", "") or i_obj.get("delta", "")

    ctr_obj = _find_stat(g_strip, 2, "ctr")
    ctr_val = ctr_obj.get("value", "")

    pos_obj = _find_stat(g_strip, 3, "position", "pos")
    pos_val = pos_obj.get("value", "")

    top_queries = gsc.get("top_queries", [])
    top_pages = gsc.get("top_pages", [])
    kw_val = kw_strip[0].get("value", "") if len(kw_strip) > 0 else ""

    # 1. Traffic Narrative
    if has_traffic:
        delta_part = f" ({sess_delta})" if sess_delta else ""
        eng_part = f" Average session duration was recorded at {duration_val} with an engagement rate of {eng_val}." if eng_val and eng_val != "N/A" else ""
        website_traffic_narrative = (
            f"According to Google Analytics 4, {comp_name} recorded {sess_val} sessions{delta_part} across {users_val} users in {curr_month}.{eng_part}"
        )
    else:
        website_traffic_narrative = "No data available from Google Analytics 4 for this period."

    # 2. GSC Narrative
    if has_gsc:
        c_part = f" ({clicks_delta})" if clicks_delta else ""
        i_part = f" ({imp_delta})" if imp_delta else ""
        gsc_narrative = (
            f"Google Search Console data indicates {clicks_val} organic search clicks{c_part} from {imp_val} search impressions{i_part}. "
            f"Average ranking position was {pos_val}, with an average CTR of {ctr_val}."
        )
    else:
        gsc_narrative = "No data available from Google Search Console for this period."

    # 3. Keyword Visibility Narrative
    if has_kw:
        keyword_visibility_narrative = f"Organic search tracking shows {comp_name} indexed for {kw_val} tracked terms."
    else:
        keyword_visibility_narrative = "No data available from SEMrush for this period."

    def _fmt(val):
        if val is None or val == "":
            return ""
        if isinstance(val, (int, float)):
            return f"{val:,}"
        return str(val)

    def _to_float(val):
        if val is None:
            return 0.0
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str):
            clean = val.replace("%", "").replace(",", "").strip()
            try:
                return float(clean)
            except ValueError:
                return 0.0
        return 0.0

    # 4. Authority Narrative
    if has_auth:
        ref_doms = auth.get("referring_domains")
        ref_str = f" with {_fmt(ref_doms)} referring domains" if ref_doms else ""
        authority_backlinks_narrative = f"The domain backlink profile reflects an Authority Score of {auth_score}/100{ref_str}."
    else:
        authority_backlinks_narrative = "No data available"

    # 5. Technical SEO Narrative
    if has_tech:
        technical_seo_narrative = f"The site achieved an overall Technical SEO Health Score of {tech_score}/100."
    else:
        technical_seo_narrative = "No data available"

    # 6. Key Improvements (Page 2 Bullets)
    key_improvements: List[str] = []
    if has_traffic:
        u_str = f"Total users reached {users_val}" + (f" ({users_delta})" if users_delta else "")
        s_str = f"sessions recorded at {sess_val}" + (f" ({sess_delta})" if sess_delta else "")
        key_improvements.append(f"Organic website traffic: {u_str} and {s_str} according to Google Analytics 4.")
        if eng_val and eng_val != "N/A":
            key_improvements.append(f"User engagement quality: Site engagement rate recorded at {eng_val} with an average session duration of {duration_val}.")

    if has_gsc:
        c_str = f"Organic search clicks reached {clicks_val}" + (f" ({clicks_delta})" if clicks_delta else "")
        i_str = f"impressions reached {imp_val}" + (f" ({imp_delta})" if imp_delta else "")
        key_improvements.append(f"Search Console visibility: {c_str} across {i_str}.")
        if pos_val:
            key_improvements.append(f"Average SERP positioning: Overall organic search position averaged {pos_val} with an average CTR of {ctr_val}.")

    if top_queries and len(top_queries) > 0:
        top_q = top_queries[0]
        q_name = top_q.get("query", "")
        q_clicks = _fmt(top_q.get("clicks", 0))
        q_imp = _fmt(top_q.get("impressions", 0))
        if q_name:
            key_improvements.append(f"Top performing query '{q_name}' generated {q_clicks} clicks from {q_imp} search impressions.")

    if has_kw:
        key_improvements.append(f"Search footprint: Domain ranked for {kw_val} organic keywords across tracked search results.")

    if has_auth and auth_score is not None:
        key_improvements.append(f"Authority metrics: Domain Authority Score established at {auth_score}/100.")

    if not key_improvements:
        key_improvements.append("No data available")

    # 7. Upcoming SEO Focus (Page 3 Bullets)
    upcoming_focus_bullets: List[str] = []
    if top_queries:
        low_ctr_queries = [q for q in top_queries if _to_float(q.get("ctr")) < 3.0 and _to_float(q.get("impressions")) > 50]
        if low_ctr_queries:
            target_q = low_ctr_queries[0].get("query", "")
            upcoming_focus_bullets.append(f"Optimize title tags and meta descriptions for high-impression query '{target_q}' to improve click-through rates (CTR).")
        striking_distance = [q for q in top_queries if 4.0 <= _to_float(q.get("position")) <= 20.0]
        if striking_distance:
            sd_q = striking_distance[0].get("query", "")
            pos_num = _to_float(striking_distance[0].get("position"))
            upcoming_focus_bullets.append(f"Target ranking query '{sd_q}' (position {pos_num:.1f}) with on-page content depth and internal linking to push toward top 3.")

    if top_pages and len(top_pages) > 0:
        p_name = top_pages[0].get("page", "")
        if p_name:
            upcoming_focus_bullets.append(f"Strengthen internal linking and topical relevance for primary organic landing page: {p_name}.")

    if not upcoming_focus_bullets:
        upcoming_focus_bullets = ["No data available for upcoming recommendations."]

    upcoming_seo_focus = {
        "intro": f"The upcoming SEO strategy for {comp_name} will focus on capitalizing on verified search performance data." if (has_traffic or has_gsc or has_kw) else "No data available",
        "bullet_points": upcoming_focus_bullets
    }

    # 8. Page 6: User Engagement Narrative (Matches GA4 Reference Layout)
    user_eng = traffic.get("user_engagement", {})
    traffic_over_time = traffic.get("traffic_over_time", [])
    if has_traffic and (user_eng.get("eventCount") is not None or user_eng.get("eventsPerSession") is not None):
        raw_events = user_eng.get("eventCount")
        tot_sess = sum(int(d.get("sessions", 0)) for d in traffic_over_time) if traffic_over_time else int(user_eng.get("sessions", 1) or 1)
        if user_eng.get("eventsPerSession") is not None:
            eps_num = float(user_eng["eventsPerSession"])
        elif raw_events is not None and tot_sess > 0:
            eps_num = raw_events / tot_sess
        else:
            eps_num = 0.0
        eps_chg = user_eng.get("eventsPerSessionChange") if user_eng.get("eventsPerSessionChange") is not None else user_eng.get("eventCountChange")
        nu_num = user_eng.get("newUsers")
        nu_chg = user_eng.get("newUsersChange")

        if nu_num is None or eps_chg is None or nu_chg is None:
            page_6_narrative = "No data available from Google Analytics 4 for user engagement."
        else:
            eps_dir = "increased" if eps_chg >= 0 else "decreased"
            nu_dir = "increased" if nu_chg >= 0 else "decreased"
            if eps_chg >= 0 and nu_chg <= 0:
                summary_sent = "Overall, user acquisition declined, but engagement per session improved."
            elif eps_chg >= 0 and nu_chg > 0:
                summary_sent = "Overall, both user acquisition and engagement per session demonstrated positive growth."
            elif eps_chg < 0 and nu_chg < 0:
                summary_sent = "Overall, user acquisition and per-session engagement saw contractions over the period."
            else:
                summary_sent = "Overall, user acquisition improved, while engagement per session normalized."

            page_6_narrative = (
                f"This graph shows GA4 performance for {curr_month}. "
                f"Events per session {eps_dir} by {abs(eps_chg):.1f}% to {eps_num:.1f}, "
                f"while new users {nu_dir} by {abs(nu_chg):.1f}% to {int(nu_num):,}. "
                f"{summary_sent}"
            )
    else:
        page_6_narrative = "No data available from Google Analytics 4 for user engagement."

    # 9. Page 7: GSC Performance Narrative (Matches GSC Reference Layout)
    if has_gsc and g_strip:
        clicks_item = next((i for i in g_strip if "click" in i.get("label", "").lower()), g_strip[0] if len(g_strip) > 0 else {})
        imp_item = next((i for i in g_strip if "impression" in i.get("label", "").lower()), g_strip[1] if len(g_strip) > 1 else {})
        ctr_item = next((i for i in g_strip if "ctr" in i.get("label", "").lower()), g_strip[2] if len(g_strip) > 2 else {})
        pos_item = next((i for i in g_strip if "position" in i.get("label", "").lower()), g_strip[3] if len(g_strip) > 3 else {})

        c_curr = clicks_item.get("value", "0")
        c_prev = clicks_item.get("prior_value", "0")
        i_curr = imp_item.get("value", "0")
        i_prev = imp_item.get("prior_value", "0")
        ctr_curr = ctr_item.get("value", "0%")
        ctr_prev = ctr_item.get("prior_value", "0%")
        p_curr = pos_item.get("value", "0")
        p_prev = pos_item.get("prior_value", "0")

        try:
            c_diff = float(str(c_curr).replace(",", "")) - float(str(c_prev).replace(",", ""))
            c_trend = "remained almost stable" if abs(c_diff) <= 2 else ("increased" if c_diff > 0 else "decreased")
        except Exception:
            c_trend = "changed"

        try:
            i_diff = float(str(i_curr).replace(",", "")) - float(str(i_prev).replace(",", ""))
            i_trend = "increased" if i_diff >= 0 else "decreased"
        except Exception:
            i_trend = "changed"

        try:
            ctr_diff = float(str(ctr_curr).replace("%", "").replace(",", "")) - float(str(ctr_prev).replace("%", "").replace(",", ""))
            ctr_trend = "improved" if ctr_diff >= 0 else "declined"
        except Exception:
            ctr_trend = "changed"

        try:
            pos_diff = float(p_curr) - float(p_prev)
            pos_trend = "improved" if pos_diff <= 0 else "declined"
        except Exception:
            pos_trend = "changed"

        prior_m = meta.get("prior_month_label") or "the prior period"
        curr_m = meta.get("curr_month_label") or curr_month
        page_7_narrative = (
            f"This graph shows GSC performance for {prior_m} vs {curr_m}. "
            f"Clicks {c_trend} ({c_prev} to {c_curr}), while impressions {i_trend} ({i_prev} to {i_curr}). "
            f"CTR {ctr_trend} from {ctr_prev} to {ctr_curr}, and average position {pos_trend} from {p_prev} to {p_curr}."
        )
    elif has_gsc:
        page_7_narrative = (
            f"Google Search Console registered {clicks_val} clicks and {imp_val} impressions during {curr_month}. "
            f"The site maintained an average ranking position of {pos_val} with an overall organic CTR of {ctr_val}."
        )
    else:
        page_7_narrative = "No data available from Google Search Console."

    # 10. Page 8: Top Queries & Pages Narrative
    page_8_narrative = (
        f"Top queries and landing pages represent the primary organic traffic drivers for {comp_name}, "
        f"highlighting queries that generate the strongest search demand and pages capturing search interest."
        if (top_queries or top_pages) else "No data available from Google Search Console for queries and landing pages."
    )

    # 11. Page 9: SEMrush Keyword Narrative
    page_9_narrative = (
        "This table provides an overview of keyword ranking distribution across different position ranges for the selected time periods."
    )

    # 12. Page 10: Branded / Non-Branded Narrative
    page_10_narrative = (
        "This section provides an overview of branded and non-branded traffic and keyword distribution across the selected time periods."
    )

    # 13. Page 11: Crucial Metrics Narrative
    sem_metrics = metrics_data.get("semrush_metrics", {})
    crucial_data = sem_metrics.get("crucial_metrics", {})
    has_crucial = any(
        isinstance(v, dict) and v.get("current") not in [None, "-", ""]
        for v in crucial_data.values()
    )
    if has_crucial:
        page_11_narrative = (
            "This section provides an overview of key SEO performance metrics, highlighting changes in authority, backlink growth, and referring domain trends across the selected time periods."
        )
    else:
        page_11_narrative = "No data available from authority and backlink tracking services."

    # 14. Page 12: Off-Page Narrative
    off_metrics = metrics_data.get("offpage_metrics", {})
    off_rows = off_metrics.get("rows", {})
    off_total = off_rows.get("total", 0)
    try:
        off_total_num = int(off_total) if str(off_total).isdigit() else 0
    except Exception:
        off_total_num = 0

    if off_total_num > 0:
        page_12_narrative = f"A total of {off_total_num} verified off-page backlink submissions were recorded for this period."
    else:
        page_12_narrative = "No off-page backlink submissions were recorded for this period."

    # 15. Page 13: Monthly SEO Summary (3 Paragraphs)
    if has_traffic or has_gsc or has_kw:
        p1 = (
            f"During {curr_month}, {comp_name} recorded active performance across its organic search channels. "
            + (f"According to Google Search Console, the domain generated {clicks_val} clicks from {imp_val} impressions with an average position of {pos_val}. " if has_gsc else "")
            + (f"Google Analytics 4 registered {sess_val} total sessions across {users_val} users." if has_traffic else "")
        )
        p2 = (
            f"Organic search visibility and keyword portfolio tracked through SEMrush reflects {kw_val} indexed keywords. "
            f"Search demand on primary queries demonstrates topical discovery across target search terms."
            if has_kw else
            f"Performance across primary organic search channels demonstrates continued search presence across target queries."
        )
        p3 = (
            f"Looking forward to next month, optimization priorities will focus on expanding click-through rates (CTR) on high-impression queries, "
            f"improving keyword rankings currently positioned on pages 2 and 3 into top-tier rankings, "
            f"and enhancing landing page engagement to ensure maximum conversion value from organic search traffic."
        )
        seo_summary_paragraphs = [p1, p2, p3]
        status_pill = f"Performance assessment recorded for {curr_month}"
        exec_summary = f"{website_traffic_narrative} {gsc_narrative}".strip()
        next_priorities = [
            {"severity": "high", "title": "CTR & Metadata Optimization", "detail": "Optimize meta title and description for top queries with low CTR to drive click gains."},
            {"severity": "med", "title": "Striking Distance Keywords", "detail": "Enhance content depth on landing pages ranking between positions 4–20 to push into the Top 3."},
            {"severity": "med", "title": "Landing Page UX & Retention", "detail": "Streamline navigation and load speed to improve GA4 engagement duration and conversion rate."}
        ]
        growth_plan = {
            "seo": ["Target high-impression queries", "Optimize top landing pages"],
            "content": ["Refresh underperforming search topics", "Expand topical authority"],
            "authority": ["Earn high-quality editorial backlinks"],
            "technical": ["Maintain Core Web Vitals compliance"]
        }
    else:
        seo_summary_paragraphs = ["No data available for executive SEO summary."]
        status_pill = "No data available"
        exec_summary = "No data available"
        next_priorities = []
        growth_plan = {"seo": [], "content": [], "authority": [], "technical": []}

    # Chips & Status Pill
    chips = []
    if has_gsc and clicks_val:
        chips.append({"type": "win", "text": f"Search Clicks: {clicks_val} {clicks_delta}".strip()})
    if has_traffic and sess_val:
        chips.append({"type": "win", "text": f"Sessions: {sess_val} {sess_delta}".strip()})
    if has_kw and kw_val:
        chips.append({"type": "win", "text": f"Ranked Keywords: {kw_val}"})

    return {
        "status_pill": status_pill,
        "executive_summary": exec_summary,
        "chips": chips,
        "key_improvements": key_improvements,
        "upcoming_seo_focus": upcoming_seo_focus,
        "page_6_engagement_narrative": page_6_narrative,
        "page_7_gsc_narrative": page_7_narrative,
        "page_8_queries_pages_narrative": page_8_narrative,
        "page_9_keyword_narrative": page_9_narrative,
        "page_10_branded_narrative": page_10_narrative,
        "page_11_crucial_metrics_narrative": page_11_narrative,
        "page_12_offpage_narrative": page_12_narrative,
        "seo_summary_paragraphs": seo_summary_paragraphs,
        "website_traffic_narrative": website_traffic_narrative,
        "gsc_narrative": gsc_narrative,
        "keyword_visibility_narrative": keyword_visibility_narrative,
        "authority_backlinks_narrative": authority_backlinks_narrative,
        "technical_seo_narrative": technical_seo_narrative,
        "work_completed": {
            "on_page": [],
            "off_page": [],
            "technical": []
        },
        "next_priorities": next_priorities,
        "growth_plan": growth_plan
    }

async def generate_monthly_seo_narratives(
    metrics_data: Dict[str, Any],
    openai_api_key: Optional[str] = None,
    model: Optional[str] = None
) -> Dict[str, Any]:
    """
    Synthesizes executive narratives from structured SEO metrics using ChatGPT API.
    Enforces strict factual accuracy and zero hallucination.
    If OPENAI_API_KEY is not available or the request fails, seamlessly falls back
    to the deterministic factual engine.
    """
    api_key = openai_api_key or os.getenv("OPENAI_API_KEY") or settings.OPENAI_API_KEY
    if not api_key:
        print("Notice: OPENAI_API_KEY is not configured; using deterministic factual narrative engine.")
        return generate_fallback_narratives(metrics_data)

    target_model = model or os.getenv("OPENAI_MODEL") or settings.OPENAI_MODEL or "gpt-4o"

    user_prompt = (
        f"You are synthesizing the executive narrative layer for an agency monthly SEO report.\n"
        f"Analyze the following verified monthly metrics:\n\n"
        f"{json.dumps(metrics_data, indent=2)}\n\n"
        f"CRITICAL INSTRUCTIONS:\n"
        f"- Absolutely ZERO hallucination. Do not invent any traffic numbers, rank numbers, or backlinks.\n"
        f"- All statistics must be strictly derived from the input JSON.\n"
        f"- For 'key_improvements': output 5 to 8 bullet points highlighting verified positive progress or steady performance from GA4, GSC, and SEMrush.\n"
        f"- For 'upcoming_seo_focus': provide a strategic intro and 6 to 8 actionable, data-backed bullet points addressing real queries, rankings, and engagement opportunities in the data.\n"
        f"- For 'seo_summary_paragraphs': provide exactly 3 well-written, professional executive paragraphs synthesizing the month's search results.\n\n"
        f"Output JSON matching this exact schema:\n"
        f"{{\n"
        f'  "status_pill": "Short high-level summary pill (e.g. \'Performance assessment: Organic search expansion\')",\n'
        f'  "executive_summary": "1-2 concise paragraphs summarizing the month\'s organic search performance.",\n'
        f'  "chips": [\n'
        f'    {{"type": "win", "text": "Win summary with exact verified number"}},\n'
        f'    {{"type": "watch", "text": "Opportunity or metric to monitor"}}\n'
        f'  ],\n'
        f'  "key_improvements": [\n'
        f'    "Bullet point 1 citing exact verified GA4/GSC/SEMrush data",\n'
        f'    "Bullet point 2 citing exact verified metrics",\n'
        f'    "Bullet point 3 citing exact verified metrics",\n'
        f'    "Bullet point 4 citing exact verified metrics",\n'
        f'    "Bullet point 5 citing exact verified metrics"\n'
        f'  ],\n'
        f'  "upcoming_seo_focus": {{\n'
        f'    "intro": "Introductory paragraph explaining upcoming strategic focus areas grounded in current data.",\n'
        f'    "bullet_points": [\n'
        f'      "Actionable focus point 1 targeting specific queries or pages from data",\n'
        f'      "Actionable focus point 2 targeting rankings from data",\n'
        f'      "Actionable focus point 3 targeting user engagement from data",\n'
        f'      "Actionable focus point 4 targeting on-page optimization",\n'
        f'      "Actionable focus point 5 targeting technical health"\n'
        f'    ]\n'
        f'  }},\n'
        f'  "page_6_engagement_narrative": "Analytical paragraph explaining GA4 user engagement and session trends.",\n'
        f'  "page_7_gsc_narrative": "Analytical paragraph explaining GSC clicks, impressions, CTR, and average position.",\n'
        f'  "page_8_queries_pages_narrative": "Analytical paragraph explaining top search queries and pages performance.",\n'
        f'  "page_9_keyword_narrative": "Analytical paragraph explaining SEMrush organic keyword distribution.",\n'
        f'  "page_10_branded_narrative": "Analytical paragraph explaining branded vs non-branded search demand.",\n'
        f'  "page_11_crucial_metrics_narrative": "Analytical paragraph explaining domain authority and backlink metrics, or \'No data available\' if unpopulated.",\n'
        f'  "page_12_offpage_narrative": "Analytical paragraph on off-page and backlink activity, or \'No data available\' if counts are 0.",\n'
        f'  "seo_summary_paragraphs": [\n'
        f'    "Paragraph 1: Comprehensive overview of organic search traffic and visibility.",\n'
        f'    "Paragraph 2: Authority, keyword ranking footprint, and search presence.",\n'
        f'    "Paragraph 3: User engagement quality and future optimization direction."\n'
        f'  ],\n'
        f'  "website_traffic_narrative": "Paragraph explaining GA4 trends.",\n'
        f'  "gsc_narrative": "Paragraph explaining GSC trends.",\n'
        f'  "keyword_visibility_narrative": "Paragraph explaining keyword visibility.",\n'
        f'  "authority_backlinks_narrative": "Paragraph explaining authority score and backlinks.",\n'
        f'  "technical_seo_narrative": "Paragraph explaining site health score.",\n'
        f'  "work_completed": {{\n'
        f'    "on_page": ["Specific optimization item grounded in data"],\n'
        f'    "off_page": ["Specific link monitoring or entity item"],\n'
        f'    "technical": ["Specific crawler or speed item"]\n'
        f'  }},\n'
        f'  "next_priorities": [\n'
        f'    {{"severity": "high", "title": "Priority title", "detail": "Implementation detail..."}},\n'
        f'    {{"severity": "med", "title": "Priority title", "detail": "Implementation detail..."}}\n'
        f'  ]\n'
        f"}}"
    )

    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": target_model,
                    "temperature": 0.2,
                    "max_tokens": 2500,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt}
                    ]
                }
            )

            if resp.status_code == 200:
                data = resp.json()
                choice = data.get("choices", [{}])[0]

                # Catch token limit truncation
                if choice.get("finish_reason") == "length":
                    print("OpenAI output was truncated due to max_tokens limit (finish_reason='length'); using deterministic fallback engine.")
                    return generate_fallback_narratives(metrics_data)

                content = choice.get("message", {}).get("content", "")
                parsed = json.loads(content)

                # Ensure required keys exist; merge with fallback defaults if any are missing
                fallback = generate_fallback_narratives(metrics_data)
                for k, v in fallback.items():
                    if k not in parsed or not parsed[k]:
                        parsed[k] = v

                return parsed
            else:
                # Catch context length errors from API response
                err_text = resp.text
                if resp.status_code == 400 and ("context_length_exceeded" in err_text or "maximum context length" in err_text.lower()):
                    print(f"OpenAI context length error ({resp.status_code}): {err_text}, using deterministic fallback engine.")
                else:
                    print(f"OpenAI API returned {resp.status_code}: {err_text}, using deterministic fallback engine.")
                return generate_fallback_narratives(metrics_data)
    except json.JSONDecodeError as e:
        print(f"OpenAI JSON parse error (response possibly truncated by token limit): {e}, using deterministic fallback engine.")
        return generate_fallback_narratives(metrics_data)
    except Exception as e:
        print(f"OpenAI narrative generation exception: {e}, using deterministic fallback engine.")
        return generate_fallback_narratives(metrics_data)
