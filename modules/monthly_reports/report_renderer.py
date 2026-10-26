import os
import re
import tempfile
from datetime import datetime
from typing import Dict, Any, Union

def get_default_agency_logo_html(agency_name: str = "SEO Reporter", size: int = 50) -> str:
    """Returns clean, generic agency branding header with no fabricated company marks."""
    return f"""
    <div style="display: inline-flex; align-items: center; gap: 12px;">
      <div style="width: {size}px; height: {size}px; border-radius: 12px; background: linear-gradient(135deg, #6366f1, #8b5cf6); display: flex; align-items: center; justify-content: center; color: #ffffff; font-weight: 800; font-size: {int(size * 0.4)}px; font-family: system-ui, sans-serif;">
        SEO
      </div>
      <div style="display: flex; flex-direction: column; line-height: 1.1;">
        <span style="font-size: 24px; font-weight: 800; color: #0f172a; letter-spacing: -0.02em; font-family: system-ui, sans-serif;">
          {agency_name}
        </span>
      </div>
    </div>
    """

def render_ga4_engagement_html(sec_traffic: Dict[str, Any], date_labels: Dict[str, Any], website: str) -> str:
    user_eng = sec_traffic.get("user_engagement", {})
    traffic_over_time = sec_traffic.get("traffic_over_time", [])
    has_traffic = bool(sec_traffic.get("status") == "connected" and (user_eng.get("eventCount") is not None or traffic_over_time))
    
    if not has_traffic:
        return """
        <div style="border: 1px dashed #cbd5e1; border-radius: 8px; padding: 48px 24px; text-align: center; background: #ffffff; margin-top: 30px;">
            <p style="font-size: 16px; font-weight: 700; color: #475569; margin-bottom: 8px;">No data available from Google Analytics 4</p>
            <p style="font-size: 13px; color: #94a3b8; margin: 0;">User engagement metrics are not available for this period.</p>
        </div>
        """

    # Real metric extraction - zero fabrication
    total_sessions = sum(int(d.get("sessions", 0)) for d in traffic_over_time) if traffic_over_time else 0
    raw_event_count = user_eng.get("eventCount")
    if user_eng.get("eventsPerSession") is not None:
        eps_val = f"{float(user_eng['eventsPerSession']):.1f}"
    elif raw_event_count is not None and total_sessions > 0:
        eps_val = f"{(raw_event_count / total_sessions):.1f}"
    else:
        eps_val = "No data available"
    eps_delta = user_eng.get("eventsPerSessionChange") if user_eng.get("eventsPerSessionChange") is not None else user_eng.get("eventCountChange")

    raw_new_users = user_eng.get("newUsers")
    new_users_val = f"{int(raw_new_users):,}" if raw_new_users is not None else "No data available"
    new_users_delta = user_eng.get("newUsersChange")

    raw_engaged_per_user = user_eng.get("engagedSessionsPerUser")
    if raw_engaged_per_user is not None:
        engaged_per_user_val = f"{float(raw_engaged_per_user):.2f}"
    elif user_eng.get("activeUsers") is not None:
        engaged_per_user_val = f"{int(user_eng['activeUsers']):,}"
    else:
        engaged_per_user_val = "No data available"
    engaged_per_user_delta = user_eng.get("engagedSessionsPerUserChange")

    def fmt_delta(delta):
        if delta is None:
            return ""
        arrow = "↑" if delta >= 0 else "↓"
        col = "#137333" if delta >= 0 else "#c5221f"
        return f'<div style="font-size: 11px; font-weight: 600; color: {col}; margin-top: 2px;">{arrow} {abs(delta):.1f}%</div>'

    width = 640
    height = 180
    pad_top = 20
    pad_right = 35
    pad_bottom = 25
    pad_left = 10

    cur_points = []
    prior_points = []
    dates = []
    for d in traffic_over_time:
        dates.append(d.get("date", ""))
        if d.get("eventsPerSession") is not None:
            cur_points.append(float(d["eventsPerSession"]))
        elif d.get("eventCount") is not None and d.get("sessions", 0) > 0:
            cur_points.append(round(float(d["eventCount"]) / float(d["sessions"]), 1))
        else:
            cur_points.append(0.0)

        if d.get("priorEventsPerSession") is not None:
            prior_points.append(float(d["priorEventsPerSession"]))
        elif d.get("priorEventCount") is not None and d.get("priorSessions", 0) > 0:
            prior_points.append(round(float(d["priorEventCount"]) / float(d["priorSessions"]), 1))
        else:
            prior_points.append(0.0)

    all_vals = cur_points + prior_points
    max_val = max(all_vals) if all_vals and max(all_vals) > 0 else 15.0
    min_val = 0.0

    y_ticks = [
        int(round(max_val)),
        int(round(max_val * 0.67)),
        int(round(max_val * 0.33)),
        0
    ]

    def build_path(pts):
        if not pts:
            return ""
        span_x = width - pad_left - pad_right
        span_y = height - pad_top - pad_bottom
        denom = max(1e-4, max_val - min_val)
        n = len(pts)
        cmds = []
        for i, v in enumerate(pts):
            x = pad_left + (i / max(1, n - 1)) * span_x
            norm = max(0.0, min(1.0, (v - min_val) / denom))
            y = (height - pad_bottom) - norm * span_y
            cmds.append(f"{'M' if i == 0 else 'L'} {x:.1f} {y:.1f}")
        return " ".join(cmds)

    cur_path = build_path(cur_points)
    prior_path = build_path(prior_points)

    y_lines_svg = []
    span_y = height - pad_top - pad_bottom
    for val in y_ticks:
        norm = (val - min_val) / max(1e-4, max_val - min_val)
        y = (height - pad_bottom) - norm * span_y
        y_lines_svg.append(f"""
        <line x1="{pad_left}" x2="{width - pad_right}" y1="{y:.1f}" y2="{y:.1f}" stroke="#f1f3f4" stroke-width="1" />
        <text x="{width - pad_right + 8}" y="{y + 4:.1f}" fill="#70757a" font-size="11" text-anchor="start" font-family="Roboto, Arial, sans-serif">{val}</text>
        """)

    circles_svg = []
    if cur_points:
        span_x = width - pad_left - pad_right
        denom = max(1e-4, max_val - min_val)
        n = len(cur_points)
        for i, v in enumerate(cur_points):
            if i % 4 == 0 or i == n - 1:
                x = pad_left + (i / max(1, n - 1)) * span_x
                norm = max(0.0, min(1.0, (v - min_val) / denom))
                y = (height - pad_bottom) - norm * span_y
                circles_svg.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="#ffffff" stroke="#1a73e8" stroke-width="2" />')

    date_labels_svg = []
    if dates:
        step = max(1, len(dates) // 4)
        span_x = width - pad_left - pad_right
        for i in range(step // 2, len(dates), step):
            x = pad_left + (i / max(1, len(dates) - 1)) * span_x
            date_labels_svg.append(f'<text x="{x:.1f}" y="{height - 6}" fill="#70757a" font-size="11" text-anchor="middle" font-family="Roboto, Arial, sans-serif">{dates[i]}</text>')

    curr_period_str = date_labels.get("curr_period_short") or "Current Period"

    return f"""
    <div style="background: #ffffff; border: 1px solid #dadce0; border-radius: 8px; box-shadow: 0 1px 2px rgba(60,64,67,0.08); padding: 20px 24px 16px 24px; margin-top: 20px; margin-bottom: 20px;">
        <!-- Header Tabs -->
        <div style="display: flex; align-items: stretch; justify-content: space-between; border-bottom: 1px solid #f1f3f4; padding-bottom: 14px;">
            <div style="display: flex; align-items: center; gap: 28px;">
                <span style="color: #dadce0; font-size: 16px; user-select: none; font-weight: bold;">&lsaquo;</span>
                <!-- Tab 1: Events per session -->
                <div style="position: relative; padding-top: 6px; padding-bottom: 4px;">
                    <div style="position: absolute; top: -6px; left: 0; right: 0; height: 3px; background: #1a73e8; border-radius: 2px;"></div>
                    <div style="display: flex; align-items: center; gap: 4px; font-size: 13px; font-weight: 600; color: #1a73e8;">
                        <span>Events per session</span>
                        <span style="font-size: 10px;">&#9662;</span>
                    </div>
                    <div style="font-size: 28px; font-weight: 700; color: #202124; margin: 4px 0 2px 0; line-height: 1.1;">
                        {eps_val}
                    </div>
                    {fmt_delta(eps_delta)}
                </div>

                <!-- Tab 2: New users -->
                <div style="position: relative; padding-top: 6px; padding-bottom: 4px;">
                    <div style="display: flex; align-items: center; gap: 4px; font-size: 13px; font-weight: 500; color: #5f6368;">
                        <span>New users</span>
                        <span style="font-size: 10px;">&#9662;</span>
                    </div>
                    <div style="font-size: 28px; font-weight: 700; color: #202124; margin: 4px 0 2px 0; line-height: 1.1;">
                        {new_users_val}
                    </div>
                    {fmt_delta(new_users_delta)}
                </div>

                <!-- Tab 3: Engaged sessions / user -->
                <div style="position: relative; padding-top: 6px; padding-bottom: 4px;">
                    <div style="display: flex; align-items: center; gap: 4px; font-size: 13px; font-weight: 500; color: #5f6368;">
                        <span>Engaged sessions / user</span>
                        <span style="font-size: 10px;">&#9662;</span>
                    </div>
                    <div style="font-size: 28px; font-weight: 700; color: #202124; margin: 4px 0 2px 0; line-height: 1.1;">
                        {engaged_per_user_val}
                    </div>
                    {fmt_delta(engaged_per_user_delta)}
                </div>
                <span style="color: #5f6368; font-size: 16px; user-select: none; font-weight: bold;">&rsaquo;</span>
            </div>

            <!-- Right Status Badges -->
            <div style="display: flex; align-items: center; gap: 8px;">
                <div style="width: 22px; height: 22px; border-radius: 50%; background: #e8eaed; display: flex; align-items: center; justify-content: center; font-size: 11px;">&#127941;</div>
                <div style="width: 22px; height: 22px; border-radius: 50%; background: #e6f4ea; display: flex; align-items: center; justify-content: center; color: #188038; font-size: 13px; font-weight: bold;">&#10004;</div>
            </div>
        </div>

        <!-- Real SVG Chart Area -->
        <div style="margin-top: 14px;">
            <svg width="100%" height="{height}" viewBox="0 0 {width} {height}" style="overflow: visible;">
                {''.join(y_lines_svg)}
                {f'<path d="{prior_path}" fill="none" stroke="#4285f4" stroke-width="1.8" stroke-dasharray="4 4" />' if prior_path else ''}
                {f'<path d="{cur_path}" fill="none" stroke="#1a73e8" stroke-width="2.5" />' if cur_path else ''}
                {''.join(circles_svg)}
                {''.join(date_labels_svg)}
            </svg>

            <!-- Legend -->
            <div style="display: flex; align-items: center; gap: 20px; margin-top: 8px; font-size: 12px; color: #3c4043;">
                <div style="display: flex; align-items: center; gap: 6px;">
                    <span style="display: inline-block; width: 16px; height: 3px; background: #1a73e8; border-radius: 1px;"></span>
                    <span>Custom</span>
                </div>
                <div style="display: flex; align-items: center; gap: 6px;">
                    <span style="display: inline-block; width: 16px; height: 0px; border-top: 2px dashed #4285f4;"></span>
                    <span>Previous period</span>
                </div>
            </div>

            <!-- Footer Bar -->
            <div style="display: flex; align-items: center; justify-content: space-between; margin-top: 14px; padding-top: 12px; border-top: 1px solid #f1f3f4;">
                <div style="display: flex; align-items: center; gap: 4px; font-size: 12px; color: #5f6368;">
                    <span>{curr_period_str}</span>
                    <span style="font-size: 10px;">&#9662;</span>
                </div>
                <div style="display: flex; align-items: center; gap: 4px; font-size: 12.5px; color: #1a73e8; font-weight: 600;">
                    <span>View reports snapshot</span>
                    <span style="font-size: 12px;">&rarr;</span>
                </div>
            </div>
        </div>
    </div>
    """

def render_gsc_performance_html(sec_gsc: Dict[str, Any], date_labels: Dict[str, Any], website: str) -> str:
    g_strip = sec_gsc.get("stat_strip", [])
    trend = sec_gsc.get("trend", [])
    has_gsc = bool(sec_gsc.get("status") == "connected" and g_strip)

    if not has_gsc:
        return """
        <div style="border: 1px dashed #cbd5e1; border-radius: 8px; padding: 48px 24px; text-align: center; background: #ffffff; margin-top: 30px;">
            <p style="font-size: 16px; font-weight: 700; color: #475569; margin-bottom: 8px;">No data available from Google Search Console</p>
            <p style="font-size: 13px; color: #94a3b8; margin: 0;">Search performance metrics are not available for this period.</p>
        </div>
        """

    clicks_item = next((i for i in g_strip if "click" in i.get("label", "").lower()), g_strip[0] if len(g_strip) > 0 else {})
    imp_item = next((i for i in g_strip if "impression" in i.get("label", "").lower()), g_strip[1] if len(g_strip) > 1 else {})
    ctr_item = next((i for i in g_strip if "ctr" in i.get("label", "").lower()), g_strip[2] if len(g_strip) > 2 else {})
    pos_item = next((i for i in g_strip if "position" in i.get("label", "").lower()), g_strip[3] if len(g_strip) > 3 else {})

    prior_period_slash = date_labels.get("prior_period_slash") or "Prior"
    curr_period_slash = date_labels.get("curr_period_slash") or "Current"

    def format_gsc_card_val(lbl: str, val: Any) -> str:
        if val is None or val == "" or val == "No data available" or val == "—":
            return "No data available"
        lbl_lower = lbl.lower()
        clean = str(val).replace(",", "").replace("%", "").strip()
        try:
            num = float(clean)
        except Exception:
            return str(val)

        if "click" in lbl_lower:
            return f"{int(round(num)):,}"
        elif "impression" in lbl_lower:
            if num >= 1000:
                return f"{num / 1000:.2f}K"
            return f"{int(round(num)):,}"
        elif "ctr" in lbl_lower:
            return f"{num:.1f}%"
        elif "position" in lbl_lower:
            return f"{num:.1f}"
        return str(val)

    cards_def = [
        ("Total clicks", "#1a73e8", format_gsc_card_val("Total clicks", clicks_item.get("prior_value")), format_gsc_card_val("Total clicks", clicks_item.get("value"))),
        ("Total impressions", "#7c3aed", format_gsc_card_val("Total impressions", imp_item.get("prior_value")), format_gsc_card_val("Total impressions", imp_item.get("value"))),
        ("Average CTR", "#00897b", format_gsc_card_val("Average CTR", ctr_item.get("prior_value")), format_gsc_card_val("Average CTR", ctr_item.get("value"))),
        ("Average position", "#ea580c", format_gsc_card_val("Average position", pos_item.get("prior_value")), format_gsc_card_val("Average position", pos_item.get("value"))),
    ]

    cards_html = []
    for lbl, bg, p_val, c_val in cards_def:
        cards_html.append(f"""
        <div style="background: {bg}; color: #ffffff; border-radius: 4px; padding: 12px 14px; position: relative; box-shadow: 0 1px 3px rgba(0,0,0,0.12);">
            <div style="display: flex; align-items: center; gap: 6px; font-size: 12px; font-weight: 600;">
                <span style="font-size: 12px;">&#9745;</span>
                <span>{lbl}</span>
            </div>
            <div style="display: flex; align-items: baseline; gap: 6px; margin-top: 8px;">
                <span style="display: inline-block; width: 12px; height: 2px; background: #ffffff;"></span>
                <span style="font-size: 18px; font-weight: 700; line-height: 1.1;">{p_val}</span>
                <span style="font-size: 10px; opacity: 0.85; margin-left: auto;">{prior_period_slash}</span>
            </div>
            <div style="display: flex; align-items: baseline; gap: 6px; margin-top: 4px;">
                <span style="display: inline-block; width: 12px; height: 0px; border-top: 2px dashed #ffffff;"></span>
                <span style="font-size: 18px; font-weight: 700; line-height: 1.1;">{c_val}</span>
                <span style="font-size: 10px; opacity: 0.85; margin-left: auto;">{curr_period_slash}</span>
            </div>
            <div style="position: absolute; bottom: 8px; right: 8px; opacity: 0.7; font-size: 11px;">&#9432;</div>
        </div>
        """)

    width = 640
    height = 220
    pad_top = 20
    pad_right = 15
    pad_bottom = 25
    pad_left = 15

    has_trend = bool(trend and len(trend) > 0)
    if has_trend:
        def build_gsc_path(extractor, inverted=False):
            vals = [extractor(d) for d in trend]
            valid = [v for v in vals if v is not None]
            if not valid:
                return ""
            min_v = min(valid)
            max_v = max(valid)
            denom = max(1e-4, max_v - min_v)
            n = len(trend)
            span_x = width - pad_left - pad_right
            span_y = height - pad_top - pad_bottom
            cmds = []
            for i, d in enumerate(trend):
                v = extractor(d)
                if v is None:
                    continue
                x = pad_left + (i / max(1, n - 1)) * span_x
                norm = max(0.0, min(1.0, (v - min_v) / denom))
                y = pad_top + norm * span_y if inverted else (height - pad_bottom) - norm * span_y
                cmds.append(f"{'M' if len(cmds) == 0 else 'L'} {x:.1f} {y:.1f}")
            return " ".join(cmds)

        p_clicks_path = build_gsc_path(lambda d: float(d["priorClicks"]) if d.get("priorClicks") is not None else None)
        c_clicks_path = build_gsc_path(lambda d: float(d["clicks"]) if d.get("clicks") is not None else None)

        p_imp_path = build_gsc_path(lambda d: float(d["priorImpressions"]) if d.get("priorImpressions") is not None else None)
        c_imp_path = build_gsc_path(lambda d: float(d["impressions"]) if d.get("impressions") is not None else None)

        p_ctr_path = build_gsc_path(lambda d: float(d.get("priorCtr")) if d.get("priorCtr") is not None else None)
        c_ctr_path = build_gsc_path(lambda d: float(d.get("ctr")) if d.get("ctr") is not None else None)

        p_pos_path = build_gsc_path(lambda d: float(d.get("priorPosition")) if d.get("priorPosition") is not None else None, inverted=True)
        c_pos_path = build_gsc_path(lambda d: float(d.get("position")) if d.get("position") is not None else None, inverted=True)

        grid_lines_svg = []
        for pct in [0, 0.25, 0.5, 0.75, 1.0]:
            y = pad_top + pct * (height - pad_top - pad_bottom)
            grid_lines_svg.append(f'<line x1="{pad_left}" x2="{width - pad_right}" y1="{y:.1f}" y2="{y:.1f}" stroke="#f1f3f4" stroke-width="1" />')

        # Left Y-axis vertical line and bottom X-axis line
        grid_lines_svg.append(f'<line x1="{pad_left}" x2="{pad_left}" y1="{pad_top}" y2="{height - pad_bottom}" stroke="#dadce0" stroke-width="1" />')
        grid_lines_svg.append(f'<line x1="{pad_left}" x2="{width - pad_right}" y1="{height - pad_bottom}" y2="{height - pad_bottom}" stroke="#dadce0" stroke-width="1" />')

        day_ticks_svg = []
        step = max(1, len(trend) // 8)
        span_x = width - pad_left - pad_right
        for i in range(2, len(trend), step):
            day_str = "".join(filter(str.isdigit, trend[i].get("date", ""))) or str(i + 1)
            x = pad_left + (i / max(1, len(trend) - 1)) * span_x
            day_ticks_svg.append(f'<text x="{x:.1f}" y="{height - 6}" fill="#70757a" font-size="11" text-anchor="middle" font-family="Roboto, Arial, sans-serif">{day_str}</text>')

        chart_html = f"""
        <div style="background: #ffffff; border: 1px solid #e0e0e0; border-radius: 6px; padding: 16px 18px; box-shadow: 0 1px 2px rgba(60,64,67,0.05);">
            <svg width="100%" height="{height}" viewBox="0 0 {width} {height}" style="overflow: visible;">
                {''.join(grid_lines_svg)}
                <!-- Clicks (Blue) -->
                {f'<path d="{p_clicks_path}" fill="none" stroke="#1a73e8" stroke-width="1.8" />' if p_clicks_path else ''}
                {f'<path d="{c_clicks_path}" fill="none" stroke="#1a73e8" stroke-width="2" stroke-dasharray="4 4" />' if c_clicks_path else ''}
                <!-- Impressions (Purple) -->
                {f'<path d="{p_imp_path}" fill="none" stroke="#7c3aed" stroke-width="1.8" />' if p_imp_path else ''}
                {f'<path d="{c_imp_path}" fill="none" stroke="#7c3aed" stroke-width="2" stroke-dasharray="4 4" />' if c_imp_path else ''}
                <!-- CTR (Teal) -->
                {f'<path d="{p_ctr_path}" fill="none" stroke="#00897b" stroke-width="1.8" />' if p_ctr_path else ''}
                {f'<path d="{c_ctr_path}" fill="none" stroke="#00897b" stroke-width="2" stroke-dasharray="4 4" />' if c_ctr_path else ''}
                <!-- Position (Orange) -->
                {f'<path d="{p_pos_path}" fill="none" stroke="#ea580c" stroke-width="1.8" />' if p_pos_path else ''}
                {f'<path d="{c_pos_path}" fill="none" stroke="#ea580c" stroke-width="2" stroke-dasharray="4 4" />' if c_pos_path else ''}
                {''.join(day_ticks_svg)}
            </svg>
        </div>
        """
    else:
        chart_html = """
        <div style="border: 1px dashed #cbd5e1; border-radius: 8px; padding: 36px 20px; text-align: center; background: #f8fafc;">
            <p style="font-size: 14px; font-weight: 700; color: #475569; margin: 0 0 4px 0;">No data available from Google Search Console</p>
            <p style="font-size: 12.5px; color: #94a3b8; margin: 0;">No search performance timeline data is available for this period.</p>
        </div>
        """

    return f"""
    <div style="margin-top: 24px; margin-bottom: 24px;">
        <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 12px;">
            {''.join(cards_html)}
        </div>
        <div style="display: flex; justify-content: flex-end; margin-bottom: 8px;">
            <div style="background: #ffffff; border: 1px solid #dadce0; border-radius: 4px; padding: 4px 10px; font-size: 12px; color: #3c4043; display: flex; align-items: center; gap: 4px;">
                <span>Daily</span>
                <span style="font-size: 10px;">&#9662;</span>
            </div>
        </div>
        {chart_html}
    </div>
    """

def build_report_page_sequence(custom_pages: list) -> list:
    """
    Builds the complete ordered sequence of pages for the monthly report.
    Interleaves standard pages (1 to 13) and custom pages based on position.
    """
    safe_custom = custom_pages if isinstance(custom_pages, list) else []
    total_pages = 13 + len(safe_custom)
    slots = [None] * total_pages
    slots[0] = {"type": "standard", "std_num": 1}

    sorted_custom = []
    for idx, cp in enumerate(safe_custom):
        pos = cp.get("position")
        if not isinstance(pos, int):
            pos = 14 + idx
        pos = max(2, min(total_pages, pos))
        sorted_custom.append({"data": cp, "desired_pos": pos, "orig_idx": idx})

    sorted_custom.sort(key=lambda x: (x["desired_pos"], x["orig_idx"]))

    for cp in sorted_custom:
        target = cp["desired_pos"] - 1
        while target < total_pages and slots[target] is not None:
            target += 1
        if target >= total_pages:
            target = cp["desired_pos"] - 1
            while target >= 1 and slots[target] is not None:
                target -= 1
        slots[target] = {"type": "custom", "data": cp["data"], "orig_idx": cp["orig_idx"]}

    std_num = 2
    for i in range(1, total_pages):
        if slots[i] is None:
            slots[i] = {"type": "standard", "std_num": std_num}
            std_num += 1

    for i, s in enumerate(slots):
        s["resolved_page_num"] = i + 1
        s["total_pages"] = total_pages

    return slots

def render_report_html(report_data: Dict[str, Any]) -> str:
    """
    Generates a 100% self-contained, standalone, vector-sharp 13-page HTML document
    representing the complete Monthly SEO Report matching the reference design.
    """
    metrics = report_data.get("metrics_data") or report_data.get("metrics") or {}
    narratives = report_data.get("narrative_data") or report_data.get("narratives") or {}
    branding = report_data.get("agency_branding") or report_data.get("branding") or {}
    meta = metrics.get("metadata", {})

    website = report_data.get("website") or meta.get("website") or ""
    clean_domain = website.replace("https://", "").replace("http://", "").split("/")[0].lower() if website else ""
    company_name = report_data.get("company_name") or meta.get("company_name") or (clean_domain.split(".")[0].capitalize() if clean_domain else "SEO Client")
    report_month = report_data.get("report_month") or meta.get("report_month") or datetime.now().strftime("%Y-%m")

    from .report_aggregator import get_period_slice
    periods_calc = get_period_slice(
        month_str=report_month,
        start_date_str=meta.get("curr_start_str") or meta.get("start_date"),
        end_date_str=meta.get("curr_end_str") or meta.get("end_date")
    )

    curr_month_label = meta.get("curr_month_label") or periods_calc["curr_month_label"]
    prior_month_label = meta.get("prior_month_label") or periods_calc["prior_month_label"]
    primary_period_label = meta.get("primary_period_label") or periods_calc["primary_period_label"]
    comparison_period_label = meta.get("comparison_period_label") or periods_calc["comparison_period_label"]
    period_label = report_data.get("period_label") or meta.get("period_label") or periods_calc["period_label"]
    primary_period_long = meta.get("primary_period_long") or periods_calc["primary_period_long"]
    prior_period_long = meta.get("prior_period_long") or periods_calc["prior_period_long"]
    curr_period_short = meta.get("curr_period_short") or periods_calc["curr_period_short"]
    prior_period_short = meta.get("prior_period_short") or periods_calc["prior_period_short"]
    curr_period_slash = meta.get("curr_period_slash") or periods_calc["curr_period_slash"]
    prior_period_slash = meta.get("prior_period_slash") or periods_calc["prior_period_slash"]

    date_labels = {
        "curr_period_slash": curr_period_slash,
        "prior_period_slash": prior_period_slash,
        "curr_period_short": curr_period_short,
        "prior_period_short": prior_period_short,
        "curr_month_label": curr_month_label,
        "prior_month_label": prior_month_label,
    }

    agency_logo_url = branding.get("logo_url", "")
    company_logo_url = report_data.get("company_logo_url") or branding.get("company_logo_url") or ""

    sec_traffic = metrics.get("section_01_website_traffic", {})
    sec_gsc = metrics.get("section_02_google_search", {})

    # Dynamic Key Improvements (Page 2)
    key_improvements = narratives.get("key_improvements", [])
    if not key_improvements:
        traffic = sec_traffic.get("stat_strip", [])
        gsc = sec_gsc.get("stat_strip", [])
        u_val = traffic[0].get("value") if traffic and traffic[0].get("value") else None
        c_val = gsc[0].get("value") if gsc and gsc[0].get("value") else None
        key_improvements = []
        if u_val:
            key_improvements.append(f"Organic website users recorded at {u_val} in Google Analytics 4.")
        if c_val:
            key_improvements.append(f"Google Search Console registered {c_val} organic search clicks.")
        if not key_improvements:
            key_improvements.append("No data available")
    key_improvements_html = "".join([f"<li>{item}</li>" for item in key_improvements])

    # Dynamic Upcoming SEO Focus (Page 3)
    upcoming_focus = narratives.get("upcoming_seo_focus", {})
    if isinstance(upcoming_focus, dict):
        upcoming_focus_intro = upcoming_focus.get("intro") or f"The upcoming SEO strategy for {company_name} will focus on capitalizing on verified search performance data."
        focus_bullets = upcoming_focus.get("bullet_points", [])
    else:
        upcoming_focus_intro = f"The upcoming SEO strategy for {company_name} will focus on optimizing keyword rankings and driving organic search traffic."
        focus_bullets = []

    if not focus_bullets:
        focus_bullets = [
            "No data available for upcoming recommendations."
        ]
    upcoming_focus_html = "".join([f"<li>{item}</li>" for item in focus_bullets])

    # Page-level narratives (Pages 6-12)
    user_eng = sec_traffic.get("user_engagement", {})
    traffic_over_time = sec_traffic.get("traffic_over_time", [])
    if narratives.get("page_6_engagement_narrative") and not narratives["page_6_engagement_narrative"].startswith("No data available"):
        p6_narrative = narratives["page_6_engagement_narrative"]
    elif sec_traffic.get("status") == "connected" and (user_eng.get("eventCount") is not None or user_eng.get("eventsPerSession") is not None):
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
            p6_narrative = "No data available from Google Analytics 4 for user engagement."
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

            p6_narrative = f"This graph shows GA4 performance for {curr_month_label}. Events per session {eps_dir} by {abs(eps_chg):.1f}% to {eps_num:.1f}, while new users {nu_dir} by {abs(nu_chg):.1f}% to {int(nu_num):,}. {summary_sent}"
    elif sec_traffic.get("status") != "connected":
        p6_narrative = "No data available from Google Analytics 4 for user engagement."
    else:
        p6_narrative = narratives.get("page_6_engagement_narrative") or "No data available from Google Analytics 4 for user engagement."

    g_strip = sec_gsc.get("stat_strip", [])
    if narratives.get("page_7_gsc_narrative") and not narratives["page_7_gsc_narrative"].startswith("No data available"):
        p7_narrative = narratives["page_7_gsc_narrative"]
    elif sec_gsc.get("status") == "connected" and g_strip:
        clicks_item = next((i for i in g_strip if "click" in i.get("label", "").lower()), g_strip[0] if len(g_strip) > 0 else {})
        imp_item = next((i for i in g_strip if "impression" in i.get("label", "").lower()), g_strip[1] if len(g_strip) > 1 else {})
        ctr_item = next((i for i in g_strip if "ctr" in i.get("label", "").lower()), g_strip[2] if len(g_strip) > 2 else {})
        pos_item = next((i for i in g_strip if "position" in i.get("label", "").lower()), g_strip[3] if len(g_strip) > 3 else {})

        if not clicks_item.get("value") and not imp_item.get("value") and not ctr_item.get("value") and not pos_item.get("value"):
            p7_narrative = "No data available from Google Search Console."
        else:
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

            p7_narrative = f"This graph shows GSC performance for {prior_month_label} vs {curr_month_label}. Clicks {c_trend} ({c_prev} to {c_curr}), while impressions {i_trend} ({i_prev} to {i_curr}). CTR {ctr_trend} from {ctr_prev} to {ctr_curr}, and average position {pos_trend} from {p_prev} to {p_curr}."
    elif sec_gsc.get("status") != "connected":
        p7_narrative = "No data available from Google Search Console."
    else:
        p7_narrative = narratives.get("page_7_gsc_narrative") or "No data available from Google Search Console."

    p8_narrative = narratives.get("page_8_queries_pages_narrative") or "No data available from Google Search Console for queries and landing pages."
    p9_narrative = (
        narratives.get("page_9_keyword_narrative")
        if narratives.get("page_9_keyword_narrative") and narratives.get("page_9_keyword_narrative") != "No data available from SEMrush for tracked keywords."
        else "This table provides an overview of keyword ranking distribution across different position ranges for the selected time periods."
    )
    p10_narrative = (
        narratives.get("page_10_branded_narrative")
        if narratives.get("page_10_branded_narrative") and narratives.get("page_10_branded_narrative") != "No data available"
        else "This section provides an overview of branded and non-branded traffic and keyword distribution across the selected time periods."
    )
    p11_narrative = (
        narratives.get("page_11_crucial_metrics_narrative")
        if narratives.get("page_11_crucial_metrics_narrative") and narratives.get("page_11_crucial_metrics_narrative") != "No data available"
        else "This section provides an overview of key SEO performance metrics, highlighting changes in authority, backlink growth, and referring domain trends across the selected time periods."
    )
    p12_narrative = (
        narratives.get("page_12_offpage_narrative")
        if narratives.get("page_12_offpage_narrative") and narratives.get("page_12_offpage_narrative") != "No data available"
        else "Detailed backlinks counts conducted during the month."
    )

    # Dynamic SEO Summary (Page 13)
    seo_summary_paragraphs = narratives.get("seo_summary_paragraphs", [])
    if not seo_summary_paragraphs or len(seo_summary_paragraphs) < 1:
        exec_sum = narratives.get("executive_summary")
        if exec_sum and exec_sum != "No data available":
            seo_summary_paragraphs = [exec_sum]
        else:
            seo_summary_paragraphs = ["No data available for executive SEO summary."]
    seo_summary_html = "".join([f'<p class="report-narrative" style="margin-top: 0; margin-bottom: 20px;">{p}</p>' for p in seo_summary_paragraphs])

    if agency_logo_url:
        top_agency_logo = f'<img src="{agency_logo_url}" style="max-height: 60px; max-width: 240px; object-fit: contain;" />'
    else:
        top_agency_logo = get_default_agency_logo_html(branding.get("agency_name") or "SEO Reporter", 52)

    if company_logo_url:
        hero_client_logo = f'<img src="{company_logo_url}" style="max-height: 180px; max-width: 360px; object-fit: contain;" />'
    else:
        hero_client_logo = """
        <div style="display: inline-flex; align-items: center; justify-content: center; padding: 30px 50px; border: 2px dashed #cbd5e1; border-radius: 8px; color: #94a3b8; font-size: 22px; font-weight: 600; letter-spacing: 0.05em;">
            Logo Here
        </div>
        """

    # -------------------------------------------------------------------------
    # Dynamic Content Blocks (Pages 4 - 12) - ZERO FABRICATED DATA
    # -------------------------------------------------------------------------
    # Page 4: User Sessions Overview (Google Analytics 4)
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

    sec_traffic = metrics.get("section_01_website_traffic", {})
    raw_user_sessions_overview = sec_traffic.get("user_sessions_overview", [])
    user_sessions_overview = merge_organic_overview_channels([
        ch for ch in raw_user_sessions_overview
        if not is_excluded_channel(ch.get("channel") or ch.get("name"))
    ])
    raw_channels = sec_traffic.get("channels", [])
    channels = merge_organic_fallback_channels([
        ch for ch in raw_channels
        if not is_excluded_channel(ch.get("channel") or ch.get("name"))
    ])
    if sec_traffic.get("status") == "connected" and user_sessions_overview:
        chan_rows = []

        for ch in user_sessions_overview:
            c_name = ch.get("channel", "Other")
            pr = ch.get("prior", {})
            cr = ch.get("current", {})
            p_label = prior_period_short or pr.get("periodLabel")
            c_label = curr_period_short or cr.get("periodLabel")

            chan_rows.append(f"""
            <tr style="background: rgba(248, 250, 252, 0.4);">
                <td rowspan="2" class="text-bold" style="text-align: left; padding-left: 14px; vertical-align: middle; border-bottom: 1px solid #cbd5e1;">{c_name}</td>
                <td style="text-align: center; font-size: 11px; color: #64748b;">{p_label}</td>
                <td style="text-align: right;">{pr.get('totalUsers', 0):,}</td>
                <td style="text-align: right;">{pr.get('newUsers', 0):,}</td>
                <td style="text-align: right;">{pr.get('returningUsers', 0):,}</td>
                <td style="text-align: right;">{pr.get('sessions', 0):,}</td>
                <td style="text-align: right;">{pr.get('engagedSessions', 0):,}</td>
                <td style="text-align: right; padding-right: 14px;">{pr.get('engagementRate', 0):.2f}%</td>
            </tr>
            <tr style="border-bottom: 1px solid #cbd5e1;">
                <td style="text-align: center; font-size: 11px; font-weight: 600; color: #4338ca;">{c_label}</td>
                <td style="text-align: right; font-weight: 600;">{cr.get('totalUsers', 0):,}</td>
                <td style="text-align: right; font-weight: 600;">{cr.get('newUsers', 0):,}</td>
                <td style="text-align: right; font-weight: 600;">{cr.get('returningUsers', 0):,}</td>
                <td style="text-align: right; font-weight: 600;">{cr.get('sessions', 0):,}</td>
                <td style="text-align: right; font-weight: 600;">{cr.get('engagedSessions', 0):,}</td>
                <td style="text-align: right; font-weight: 600; padding-right: 14px;">{cr.get('engagementRate', 0):.2f}%</td>
            </tr>
            """)

        page_4_body = f"""
        <table class="report-table" style="font-size: 11px; margin-top: 20px;">
            <thead>
                <tr>
                    <th style="width: 22%; text-align: left; padding-left: 14px;">Traffic Channel</th>
                    <th style="width: 16%; text-align: center;">Month</th>
                    <th style="width: 10%; text-align: right;">Total Users</th>
                    <th style="width: 10%; text-align: right;">New Users</th>
                    <th style="width: 11%; text-align: right;">Returning Users</th>
                    <th style="width: 10%; text-align: right;">Session</th>
                    <th style="width: 11%; text-align: right;">Engaged Sessions</th>
                    <th style="width: 10%; text-align: right; padding-right: 14px;">Engagement Rate</th>
                </tr>
            </thead>
            <tbody>
                {''.join(chan_rows)}
            </tbody>
        </table>
        """
    elif sec_traffic.get("status") == "connected" and channels:
        tot_sessions = sum(ch.get("sessions") or 0 for ch in channels)
        chan_rows = []
        for ch in channels:
            c_name = ch.get("channel") or ch.get("name") or "Other"
            c_sess = ch.get("sessions") or 0
            c_share = f"{(c_sess / tot_sessions * 100):.1f}%" if tot_sessions > 0 else "0.0%"
            chan_rows.append(f"""
            <tr>
                <td class="text-bold" style="text-align: left; padding-left: 16px;">{c_name}</td>
                <td style="text-align: center;">{curr_period_short}</td>
                <td style="text-align: right; padding-right: 16px;">{c_sess:,}</td>
                <td style="text-align: right; padding-right: 16px;">{c_share}</td>
            </tr>
            """)
        page_4_body = f"""
        <table class="report-table" style="margin-top: 24px;">
            <thead>
                <tr>
                    <th style="width: 35%; text-align: left; padding-left: 16px;">Traffic Channel</th>
                    <th style="width: 25%; text-align: center;">Period</th>
                    <th style="width: 20%; text-align: right; padding-right: 16px;">Sessions</th>
                    <th style="width: 20%; text-align: right; padding-right: 16px;">Traffic Share</th>
                </tr>
            </thead>
            <tbody>
                {''.join(chan_rows)}
                <tr style="background: #f8fafc; font-weight: 700;">
                    <td class="text-bold" style="text-align: left; padding-left: 16px;">Total</td>
                    <td style="text-align: center;">{curr_period_short}</td>
                    <td style="text-align: right; padding-right: 16px;">{tot_sessions:,}</td>
                    <td style="text-align: right; padding-right: 16px;">100.0%</td>
                </tr>
            </tbody>
        </table>
        """
    else:
        page_4_body = """
        <div style="border: 1px dashed #cbd5e1; border-radius: 8px; padding: 48px 24px; text-align: center; background: #ffffff; margin-top: 30px;">
            <p style="font-size: 16px; font-weight: 700; color: #475569; margin-bottom: 8px;">No data available from Google Analytics 4</p>
            <p style="font-size: 13px; color: #94a3b8; margin: 0;">No traffic channel data is available for this period.</p>
        </div>
        """

    # Page 5: Geographical Insights (Users by Country)
    geographical_insights = sec_traffic.get("geographical_insights", [])
    countries = sec_traffic.get("countries", [])
    base_country = (
        report_data.get("company_base_country")
        or narratives.get("company_base_country")
        or branding.get("company_base_country")
        or (report_data.get("metrics_data", {}).get("metadata", {}).get("company_base_country"))
        or ""
    ).strip()

    if sec_traffic.get("status") == "connected" and geographical_insights:
        sorted_geo = list(geographical_insights)
        if base_country:
            base_lower = base_country.lower()
            match_idx = next(
                (i for i, c in enumerate(sorted_geo) if (c.get("country") or "").strip().lower() == base_lower),
                None
            )
            if match_idx is not None:
                matched_item = sorted_geo.pop(match_idx)
                sorted_geo = [matched_item] + sorted_geo
            else:
                synth_item = {
                    "country": base_country,
                    "prior": {
                        "periodLabel": prior_period_short,
                        "activeUsers": 0,
                        "newUsers": 0,
                        "engagedSessions": 0,
                        "engagementRate": 0.0
                    },
                    "current": {
                        "periodLabel": curr_period_short,
                        "activeUsers": 0,
                        "newUsers": 0,
                        "engagedSessions": 0,
                        "engagementRate": 0.0
                    }
                }
                sorted_geo = [synth_item] + sorted_geo

        country_rows = []
        for c in sorted_geo[:5]:
            c_name = c.get("country", "Unknown")
            pr = c.get("prior", {})
            cr = c.get("current", {})
            p_label = prior_period_short or pr.get("periodLabel")
            c_label = curr_period_short or cr.get("periodLabel")

            country_rows.append(f"""
            <tr style="background: rgba(248, 250, 252, 0.4);">
                <td rowspan="2" class="text-bold" style="text-align: left; padding-left: 14px; vertical-align: middle; border-bottom: 1px solid #cbd5e1;">{c_name}</td>
                <td style="text-align: center; font-size: 11px; color: #64748b;">{p_label}</td>
                <td style="text-align: right;">{pr.get('activeUsers', 0):,}</td>
                <td style="text-align: right;">{pr.get('newUsers', 0):,}</td>
                <td style="text-align: right;">{pr.get('engagedSessions', 0):,}</td>
                <td style="text-align: right; padding-right: 14px;">{pr.get('engagementRate', 0):.2f}%</td>
            </tr>
            <tr style="border-bottom: 1px solid #cbd5e1;">
                <td style="text-align: center; font-size: 11px; font-weight: 600; color: #4338ca;">{c_label}</td>
                <td style="text-align: right; font-weight: 600;">{cr.get('activeUsers', 0):,}</td>
                <td style="text-align: right; font-weight: 600;">{cr.get('newUsers', 0):,}</td>
                <td style="text-align: right; font-weight: 600;">{cr.get('engagedSessions', 0):,}</td>
                <td style="text-align: right; font-weight: 600; padding-right: 14px;">{cr.get('engagementRate', 0):.2f}%</td>
            </tr>
            """)

        page_5_body = f"""
        <table class="report-table" style="font-size: 11.5px; margin-top: 20px;">
            <thead>
                <tr>
                    <th style="width: 25%; text-align: left; padding-left: 14px;">Country</th>
                    <th style="width: 18%; text-align: center;">Month</th>
                    <th style="width: 14%; text-align: right;">Active User</th>
                    <th style="width: 14%; text-align: right;">New Users</th>
                    <th style="width: 15%; text-align: right;">Engaged Session</th>
                    <th style="width: 14%; text-align: right; padding-right: 14px;">Engagement Rate</th>
                </tr>
            </thead>
            <tbody>
                {''.join(country_rows)}
            </tbody>
        </table>
        """
    elif sec_traffic.get("status") == "connected" and countries:
        sorted_cnt = list(countries)
        if base_country:
            base_lower = base_country.lower()
            match_idx = next(
                (i for i, c in enumerate(sorted_cnt) if (c.get("name") or c.get("country") or "").strip().lower() == base_lower),
                None
            )
            if match_idx is not None:
                matched_item = sorted_cnt.pop(match_idx)
                sorted_cnt = [matched_item] + sorted_cnt
            else:
                sorted_cnt = [{"name": base_country, "country": base_country, "sessions": 0, "value": 0}] + sorted_cnt
        display_cnt = sorted_cnt[:5]
        tot_country_sess = sum(c.get("sessions") or c.get("value") or 0 for c in display_cnt)
        country_rows = []
        for c in display_cnt:
            c_name = c.get("name") or c.get("country") or "Unknown"
            c_val = c.get("sessions") or c.get("value") or 0
            c_share = f"{(c_val / tot_country_sess * 100):.1f}%" if tot_country_sess > 0 else "0.0%"
            country_rows.append(f"""
            <tr>
                <td class="text-bold" style="text-align: left; padding-left: 16px;">{c_name}</td>
                <td style="text-align: center;">{curr_period_short}</td>
                <td style="text-align: right; padding-right: 16px;">{c_val:,}</td>
                <td style="text-align: right; padding-right: 16px;">{c_share}</td>
            </tr>
            """)
        page_5_body = f"""
        <table class="report-table" style="margin-top: 24px;">
            <thead>
                <tr>
                    <th style="width: 35%; text-align: left; padding-left: 16px;">Country</th>
                    <th style="width: 25%; text-align: center;">Period</th>
                    <th style="width: 20%; text-align: right; padding-right: 16px;">Users / Sessions</th>
                    <th style="width: 20%; text-align: right; padding-right: 16px;">Share</th>
                </tr>
            </thead>
            <tbody>
                {''.join(country_rows)}
                <tr style="background: #f8fafc; font-weight: 700;">
                    <td class="text-bold" style="text-align: left; padding-left: 16px;">Total</td>
                    <td style="text-align: center;">{curr_period_short}</td>
                    <td style="text-align: right; padding-right: 16px;">{tot_country_sess:,}</td>
                    <td style="text-align: right; padding-right: 16px;">100.0%</td>
                </tr>
            </tbody>
        </table>
        """
    else:
        page_5_body = """
        <div style="border: 1px dashed #cbd5e1; border-radius: 8px; padding: 48px 24px; text-align: center; background: #ffffff; margin-top: 30px;">
            <p style="font-size: 16px; font-weight: 700; color: #475569; margin-bottom: 8px;">No data available from Google Analytics 4</p>
            <p style="font-size: 13px; color: #94a3b8; margin: 0;">No geographical location metrics are available for this period.</p>
        </div>
        """

    # Page 6: User Engagement and Performance
    page_6_body = render_ga4_engagement_html(sec_traffic, date_labels, website)

    # Page 7: Google Search Console Overview
    page_7_body = render_gsc_performance_html(sec_gsc, date_labels, website)

    # Page 8: Top Queries & Top Pages (Comparative with Clicks & Impressions differences)
    top_queries = sec_gsc.get("top_queries", [])
    top_pages = sec_gsc.get("top_pages", [])
    if sec_gsc.get("status") == "connected" and (top_queries or top_pages):
        query_rows = []
        for q in top_queries[:10]:
            q_name = q.get("query") or q.get("keyword") or ""
            c_prev = q.get("clicks_prev", 0)
            c_curr = q.get("clicks_curr") if q.get("clicks_curr") is not None else q.get("clicks", 0)
            c_diff = q.get("clicks_diff") if q.get("clicks_diff") is not None else (c_curr - c_prev)
            i_prev = q.get("impressions_prev", 0)
            i_curr = q.get("impressions_curr") if q.get("impressions_curr") is not None else q.get("impressions", 0)
            i_diff = q.get("impressions_diff") if q.get("impressions_diff") is not None else (i_curr - i_prev)

            c_diff_str = f"+{c_diff:,}" if c_diff > 0 else f"{c_diff:,}"
            c_diff_col = "#16a34a" if c_diff > 0 else "#dc2626" if c_diff < 0 else "#64748b"
            i_diff_str = f"+{i_diff:,}" if i_diff > 0 else f"{i_diff:,}"
            i_diff_col = "#16a34a" if i_diff > 0 else "#dc2626" if i_diff < 0 else "#64748b"

            query_rows.append(f"""
            <tr>
                <td class="text-left text-bold" style="padding-left: 10px;">{q_name}</td>
                <td style="text-align: right;">{c_prev:,}</td>
                <td style="text-align: right; font-weight: 600;">{c_curr:,}</td>
                <td style="text-align: right; font-weight: 700; color: {c_diff_col};">{c_diff_str}</td>
                <td style="text-align: right;">{i_prev:,}</td>
                <td style="text-align: right; font-weight: 600;">{i_curr:,}</td>
                <td style="text-align: right; font-weight: 700; color: {i_diff_col}; padding-right: 10px;">{i_diff_str}</td>
            </tr>
            """)

        page_rows = []
        for p in top_pages[:8]:
            p_name = p.get("page", "")
            c_prev = p.get("clicks_prev", 0)
            c_curr = p.get("clicks_curr") if p.get("clicks_curr") is not None else p.get("clicks", 0)
            c_diff = p.get("clicks_diff") if p.get("clicks_diff") is not None else (c_curr - c_prev)
            i_prev = p.get("impressions_prev", 0)
            i_curr = p.get("impressions_curr") if p.get("impressions_curr") is not None else p.get("impressions", 0)
            i_diff = p.get("impressions_diff") if p.get("impressions_diff") is not None else (i_curr - i_prev)

            c_diff_str = f"+{c_diff:,}" if c_diff > 0 else f"{c_diff:,}"
            c_diff_col = "#16a34a" if c_diff > 0 else "#dc2626" if c_diff < 0 else "#64748b"
            i_diff_str = f"+{i_diff:,}" if i_diff > 0 else f"{i_diff:,}"
            i_diff_col = "#16a34a" if i_diff > 0 else "#dc2626" if i_diff < 0 else "#64748b"

            page_rows.append(f"""
            <tr>
                <td class="text-left" style="font-family: monospace; font-size: 10px; word-break: break-all; padding-left: 10px;">{p_name}</td>
                <td style="text-align: right;">{c_prev:,}</td>
                <td style="text-align: right; font-weight: 600;">{c_curr:,}</td>
                <td style="text-align: right; font-weight: 700; color: {c_diff_col};">{c_diff_str}</td>
                <td style="text-align: right;">{i_prev:,}</td>
                <td style="text-align: right; font-weight: 600;">{i_curr:,}</td>
                <td style="text-align: right; font-weight: 700; color: {i_diff_col}; padding-right: 10px;">{i_diff_str}</td>
            </tr>
            """)

        page_8_body = f"""
        <div class="report-subtitle-purple" style="margin-top: 25px; margin-bottom: 12px;">
            <u>Top Queries Performance:</u>
        </div>
        <table class="report-table" style="font-size: 11px; margin-bottom: 22px;">
            <thead>
                <tr>
                    <th style="text-align: left; width: 31%; padding-left: 10px;">Top Queries</th>
                    <th style="width: 11%; text-align: right;">Clicks ({prior_period_slash})</th>
                    <th style="width: 11%; text-align: right;">Clicks ({curr_period_slash})</th>
                    <th style="width: 12%; text-align: right;">Clicks Difference</th>
                    <th style="width: 11%; text-align: right;">Impr. ({prior_period_slash})</th>
                    <th style="width: 11%; text-align: right;">Impr. ({curr_period_slash})</th>
                    <th style="width: 13%; text-align: right; padding-right: 10px;">Impr. Difference</th>
                </tr>
            </thead>
            <tbody>
                {''.join(query_rows) if query_rows else '<tr><td colspan="7" style="text-align: center; color: #94a3b8;">No query data available</td></tr>'}
            </tbody>
        </table>

        <div class="report-subtitle-purple" style="margin-bottom: 12px;">
            <u>Top Pages Performance:</u>
        </div>
        <table class="report-table" style="font-size: 10.5px; margin-bottom: 20px;">
            <thead>
                <tr>
                    <th style="text-align: left; width: 31%; padding-left: 10px;">Top Pages</th>
                    <th style="width: 11%; text-align: right;">Clicks ({prior_period_slash})</th>
                    <th style="width: 11%; text-align: right;">Clicks ({curr_period_slash})</th>
                    <th style="width: 12%; text-align: right;">Clicks Difference</th>
                    <th style="width: 11%; text-align: right;">Impr. ({prior_period_slash})</th>
                    <th style="width: 11%; text-align: right;">Impr. ({curr_period_slash})</th>
                    <th style="width: 13%; text-align: right; padding-right: 10px;">Impr. Difference</th>
                </tr>
            </thead>
            <tbody>
                {''.join(page_rows) if page_rows else '<tr><td colspan="7" style="text-align: center; color: #94a3b8;">No landing page data available</td></tr>'}
            </tbody>
        </table>
        """
    else:
        page_8_body = """
        <div style="border: 1px dashed #cbd5e1; border-radius: 8px; padding: 48px 24px; text-align: center; background: #ffffff; margin-top: 30px;">
            <p style="font-size: 16px; font-weight: 700; color: #475569; margin-bottom: 8px;">No data available from Google Search Console</p>
            <p style="font-size: 13px; color: #94a3b8; margin: 0;">No query or landing page data is available for this period.</p>
        </div>
        """

    # -------------------------------------------------------------
    # Semrush & Third-Party Metrics (Pages 9, 10, 11)
    # -------------------------------------------------------------
    sem_data = narratives.get("semrush_metrics") or metrics.get("semrush_metrics") or {}

    col_prior = sem_data.get("prior_period_label") or comparison_period_label or prior_period_short or "Prior Period"
    col_curr = sem_data.get("current_period_label") or primary_period_label or curr_period_short or "Current Period"
    footnote_text = sem_data.get("footnote") or "*Data taken from verified search telemetry"

    # Page 9: SEMrush Organic Keyword
    org_kw = sem_data.get("organic_keywords", {})
    t1_3 = org_kw.get("top_1_3", {"prior": 0, "current": 0})
    t4_10 = org_kw.get("top_4_10", {"prior": 0, "current": 0})
    t11_20 = org_kw.get("top_11_20", {"prior": 0, "current": 0})
    t21_50 = org_kw.get("top_21_50", {"prior": 0, "current": 0})
    t51_100 = org_kw.get("top_51_100", {"prior": 0, "current": 0})
    serp_f = org_kw.get("serp_features", {"prior": 0, "current": 0})
    tot_kw = org_kw.get("total", {"prior": 0, "current": 0})

    page_9_body = f"""
    <table class="report-table" style="max-width: 580px; margin: 0 0 16px 0;">
        <thead>
            <tr>
                <th style="text-align: left; padding-left: 16px; width: 40%;">Keywords</th>
                <th style="text-align: center; width: 30%;">{col_prior}</th>
                <th style="text-align: center; width: 30%;">{col_curr}</th>
            </tr>
        </thead>
        <tbody>
            <tr>
                <td class="text-left text-bold" style="padding-left: 16px;">Top 1 - 3</td>
                <td style="text-align: center;">{t1_3.get('prior', 0)}</td>
                <td style="text-align: center;">{t1_3.get('current', 0)}</td>
            </tr>
            <tr>
                <td class="text-left text-bold" style="padding-left: 16px;">4 - 10</td>
                <td style="text-align: center;">{t4_10.get('prior', 0)}</td>
                <td style="text-align: center;">{t4_10.get('current', 0)}</td>
            </tr>
            <tr>
                <td class="text-left text-bold" style="padding-left: 16px;">11 - 20</td>
                <td style="text-align: center;">{t11_20.get('prior', 0)}</td>
                <td style="text-align: center;">{t11_20.get('current', 0)}</td>
            </tr>
            <tr>
                <td class="text-left text-bold" style="padding-left: 16px;">21- 50</td>
                <td style="text-align: center;">{t21_50.get('prior', 0)}</td>
                <td style="text-align: center;">{t21_50.get('current', 0)}</td>
            </tr>
            <tr>
                <td class="text-left text-bold" style="padding-left: 16px;">51 - 100</td>
                <td style="text-align: center;">{t51_100.get('prior', 0)}</td>
                <td style="text-align: center;">{t51_100.get('current', 0)}</td>
            </tr>
            <tr>
                <td class="text-left text-bold" style="padding-left: 16px;">SERP Features</td>
                <td style="text-align: center;">{serp_f.get('prior', 0)}</td>
                <td style="text-align: center;">{serp_f.get('current', 0)}</td>
            </tr>
            <tr>
                <td class="text-left text-bold" style="padding-left: 16px;">Total</td>
                <td style="text-align: center; font-weight: 700;">{tot_kw.get('prior', 0)}</td>
                <td style="text-align: center; font-weight: 700;">{tot_kw.get('current', 0)}</td>
            </tr>
        </tbody>
    </table>
    <div class="report-footnote">{footnote_text}</div>
    """

    # Page 10: Branded and Non-Branded
    bnb = sem_data.get("branded_non_branded", {})
    b_traf = bnb.get("branded_traffic", {"prior": "0", "current": "0"})
    nb_traf = bnb.get("non_branded_traffic", {"prior": "0", "current": "0"})
    b_kw = bnb.get("branded_keywords", {"prior": "0", "current": "0"})
    nb_kw = bnb.get("non_branded_keywords", {"prior": "0", "current": "0"})

    page_10_body = f"""
    <table class="report-table" style="max-width: 580px; margin: 0 0 24px 0;">
        <thead>
            <tr>
                <th style="text-align: left; padding-left: 16px; width: 40%;"></th>
                <th style="text-align: center; width: 30%;">{col_prior}</th>
                <th style="text-align: center; width: 30%;">{col_curr}</th>
            </tr>
        </thead>
        <tbody>
            <tr>
                <td class="text-left text-bold" style="padding-left: 16px;">Branded Traffic</td>
                <td style="text-align: center;">{b_traf.get('prior', '0')}</td>
                <td style="text-align: center;">{b_traf.get('current', '0')}</td>
            </tr>
            <tr>
                <td class="text-left text-bold" style="padding-left: 16px;">Non- Branded Traffic</td>
                <td style="text-align: center;">{nb_traf.get('prior', '0')}</td>
                <td style="text-align: center;">{nb_traf.get('current', '0')}</td>
            </tr>
        </tbody>
    </table>

    <table class="report-table" style="max-width: 580px; margin: 0 0 16px 0;">
        <thead>
            <tr>
                <th style="text-align: left; padding-left: 16px; width: 40%;"></th>
                <th style="text-align: center; width: 30%;">{col_prior}</th>
                <th style="text-align: center; width: 30%;">{col_curr}</th>
            </tr>
        </thead>
        <tbody>
            <tr>
                <td class="text-left text-bold" style="padding-left: 16px;">Branded Keyword</td>
                <td style="text-align: center;">{b_kw.get('prior', '0')}</td>
                <td style="text-align: center;">{b_kw.get('current', '0')}</td>
            </tr>
            <tr>
                <td class="text-left text-bold" style="padding-left: 16px;">Non- Branded Keyword</td>
                <td style="text-align: center;">{nb_kw.get('prior', '0')}</td>
                <td style="text-align: center;">{nb_kw.get('current', '0')}</td>
            </tr>
        </tbody>
    </table>
    <div class="report-footnote">{footnote_text}</div>
    """

    # Page 11: Crucial Metric
    crucial = sem_data.get("crucial_metrics", {})
    dr = crucial.get("domain_rating", {"prior": "-", "current": "-"})
    da = crucial.get("domain_authority", {"prior": "-", "current": "-"})
    pa = crucial.get("page_authority", {"prior": "-", "current": "-"})
    spam = crucial.get("spam_score", {"prior": "-", "current": "-"})
    auth_s = crucial.get("authority_score", {"prior": "-", "current": "-"})
    tbl = crucial.get("total_backlinks", {"prior": "-", "current": "-"})
    nbl = crucial.get("new_backlinks", {"prior": "-", "current": "-"})
    nrd = crucial.get("new_referring_domains", {"prior": "-", "current": "-"})

    page_11_body = f"""
    <table class="report-table" style="max-width: 580px; margin: 0 0 16px 0;">
        <thead>
            <tr>
                <th style="text-align: left; padding-left: 16px; width: 40%;">Attributes</th>
                <th style="text-align: center; width: 30%;">{col_prior}</th>
                <th style="text-align: center; width: 30%;">{col_curr}</th>
            </tr>
        </thead>
        <tbody>
            <tr>
                <td class="text-left text-bold" style="padding-left: 16px;">Domain Rating</td>
                <td style="text-align: center;">{dr.get('prior', '-')}</td>
                <td style="text-align: center;">{dr.get('current', '-')}</td>
            </tr>
            <tr>
                <td class="text-left text-bold" style="padding-left: 16px;">Domain Authority</td>
                <td style="text-align: center;">{da.get('prior', '-')}</td>
                <td style="text-align: center;">{da.get('current', '-')}</td>
            </tr>
            <tr>
                <td class="text-left text-bold" style="padding-left: 16px;">Page Authority</td>
                <td style="text-align: center;">{pa.get('prior', '-')}</td>
                <td style="text-align: center;">{pa.get('current', '-')}</td>
            </tr>
            <tr>
                <td class="text-left text-bold" style="padding-left: 16px;">Spam Score</td>
                <td style="text-align: center;">{spam.get('prior', '-')}</td>
                <td style="text-align: center;">{spam.get('current', '-')}</td>
            </tr>
            <tr>
                <td class="text-left text-bold" style="padding-left: 16px;">Authority Score</td>
                <td style="text-align: center;">{auth_s.get('prior', '-')}</td>
                <td style="text-align: center;">{auth_s.get('current', '-')}</td>
            </tr>
            <tr>
                <td class="text-left text-bold" style="padding-left: 16px;">Total Backlinks</td>
                <td style="text-align: center;">{tbl.get('prior', '-')}</td>
                <td style="text-align: center;">{tbl.get('current', '-')}</td>
            </tr>
            <tr>
                <td class="text-left text-bold" style="padding-left: 16px;">New Backlinks</td>
                <td style="text-align: center;">{nbl.get('prior', '-')}</td>
                <td style="text-align: center;">{nbl.get('current', '-')}</td>
            </tr>
            <tr>
                <td class="text-left text-bold" style="padding-left: 16px;">New Referring Domain</td>
                <td style="text-align: center;">{nrd.get('prior', '-')}</td>
                <td style="text-align: center;">{nrd.get('current', '-')}</td>
            </tr>
        </tbody>
    </table>
    <div class="report-footnote">{footnote_text}</div>
    """

    # Page 12: Off Page
    off_data = narratives.get("offpage_metrics") or metrics.get("offpage_metrics") or {}
    off_period = off_data.get("period_label") or primary_period_label or curr_period_short or "Current Period"
    off_narrative = off_data.get("narrative") or p12_narrative
    off_link_text = off_data.get("report_link_text") or f"{company_name} Backlinks Report"
    off_link_url = off_data.get("report_link_url") or ""
    off_rows = off_data.get("rows") or {}

    b_listing = off_rows.get("business_listing", 0)
    b_profile = off_rows.get("profile", 0)
    b_blog = off_rows.get("blog", 0)
    b_pdf = off_rows.get("pdf", 0)
    b_forum = off_rows.get("forum", 0)
    b_quora = off_rows.get("quora", 0)
    b_pinterest = off_rows.get("pinterest", 0)
    b_infographic = off_rows.get("image_infographic", 0)

    try:
        categories_sum = sum([
            int(b_listing) if str(b_listing).isdigit() else 0,
            int(b_profile) if str(b_profile).isdigit() else 0,
            int(b_blog) if str(b_blog).isdigit() else 0,
            int(b_pdf) if str(b_pdf).isdigit() else 0,
            int(b_forum) if str(b_forum).isdigit() else 0,
            int(b_quora) if str(b_quora).isdigit() else 0,
            int(b_pinterest) if str(b_pinterest).isdigit() else 0,
            int(b_infographic) if str(b_infographic).isdigit() else 0,
        ])
    except Exception:
        categories_sum = 0

    b_total = off_rows.get("total", categories_sum)

    if off_link_url:
        report_link_html = f'<a href="{off_link_url}" target="_blank" rel="noopener noreferrer" style="color: #2563eb; text-decoration: underline; font-weight: 700; font-size: 15px;">{off_link_text}</a>'
    else:
        report_link_html = f'<a href="#" onclick="return false;" style="color: #2563eb; text-decoration: underline; font-weight: 700; font-size: 15px;">{off_link_text}</a>'

    page_12_body = f"""
    <div style="margin-top: 15px; width: 100%;">
        <table class="report-table" style="width: 100%; border: 1.5px solid #000; border-collapse: collapse; margin-bottom: 24px;">
            <thead>
                <tr>
                    <th style="width: 50%; text-align: center; font-weight: 700; font-size: 14px; padding: 10px; border: 1.5px solid #000; background: #ffffff;">Backlinks</th>
                    <th style="width: 50%; text-align: center; font-weight: 700; font-size: 14px; padding: 10px; border: 1.5px solid #000; background: #ffffff;">{off_period}</th>
                </tr>
            </thead>
            <tbody>
                <tr>
                    <td style="text-align: center; font-weight: 700; font-size: 13.5px; padding: 8px 10px; border: 1.5px solid #000;">Business Listing</td>
                    <td style="text-align: center; font-size: 13.5px; padding: 8px 10px; border: 1.5px solid #000;">{b_listing}</td>
                </tr>
                <tr>
                    <td style="text-align: center; font-weight: 700; font-size: 13.5px; padding: 8px 10px; border: 1.5px solid #000;">Profile</td>
                    <td style="text-align: center; font-size: 13.5px; padding: 8px 10px; border: 1.5px solid #000;">{b_profile}</td>
                </tr>
                <tr>
                    <td style="text-align: center; font-weight: 700; font-size: 13.5px; padding: 8px 10px; border: 1.5px solid #000;">Blog</td>
                    <td style="text-align: center; font-size: 13.5px; padding: 8px 10px; border: 1.5px solid #000;">{b_blog}</td>
                </tr>
                <tr>
                    <td style="text-align: center; font-weight: 700; font-size: 13.5px; padding: 8px 10px; border: 1.5px solid #000;">PDF</td>
                    <td style="text-align: center; font-size: 13.5px; padding: 8px 10px; border: 1.5px solid #000;">{b_pdf}</td>
                </tr>
                <tr>
                    <td style="text-align: center; font-weight: 700; font-size: 13.5px; padding: 8px 10px; border: 1.5px solid #000;">Forum</td>
                    <td style="text-align: center; font-size: 13.5px; padding: 8px 10px; border: 1.5px solid #000;">{b_forum}</td>
                </tr>
                <tr>
                    <td style="text-align: center; font-weight: 700; font-size: 13.5px; padding: 8px 10px; border: 1.5px solid #000;">Quora</td>
                    <td style="text-align: center; font-size: 13.5px; padding: 8px 10px; border: 1.5px solid #000;">{b_quora}</td>
                </tr>
                <tr>
                    <td style="text-align: center; font-weight: 700; font-size: 13.5px; padding: 8px 10px; border: 1.5px solid #000;">Pinterest</td>
                    <td style="text-align: center; font-size: 13.5px; padding: 8px 10px; border: 1.5px solid #000;">{b_pinterest}</td>
                </tr>
                <tr>
                    <td style="text-align: center; font-weight: 700; font-size: 13.5px; padding: 8px 10px; border: 1.5px solid #000;">Image/Infographic</td>
                    <td style="text-align: center; font-size: 13.5px; padding: 8px 10px; border: 1.5px solid #000;">{b_infographic}</td>
                </tr>
                <tr>
                    <td style="text-align: center; font-weight: 700; font-size: 14px; padding: 9px 10px; border: 1.5px solid #000;">Total</td>
                    <td style="text-align: center; font-weight: 700; font-size: 14px; padding: 9px 10px; border: 1.5px solid #000;">{b_total}</td>
                </tr>
            </tbody>
        </table>

        <p class="report-narrative" style="margin-top: 18px; margin-bottom: 22px; font-size: 13.5px; color: #111827; text-align: left;">
            {off_narrative}
        </p>

        <div style="margin-top: 12px; text-align: left;">
            {report_link_html}
        </div>
    </div>
    """

    # Dynamic Custom Pages
    custom_pages = narratives.get("custom_pages", []) or []
    total_pages = 13 + len(custom_pages)

    custom_page_bodies = []
    for c_idx, cp in enumerate(custom_pages):
        cp_title = cp.get("title") or f"Custom Page {c_idx + 1}"
        cp_subtitle = cp.get("subtitle") or ""
        cp_intro = cp.get("narrative_intro") or ""

        cp_sub_html = f'<div style="font-size: 13.5px; color: #64748b; font-weight: 600; margin-bottom: 22px;">{cp_subtitle}</div>' if cp_subtitle else ""
        cp_intro_html = f'<p class="report-narrative" style="margin-top: 0; margin-bottom: 20px;">{cp_intro}</p>' if cp_intro else ""

        # KPI grid
        cp_metrics_html = ""
        cp_metrics = cp.get("metrics") or []
        if cp_metrics:
            cards = []
            for m in cp_metrics:
                d_cls = "positive" if m.get("delta_type") == "positive" else ("negative" if m.get("delta_type") == "negative" else "")
                delta_h = f'<span class="custom-preview-kpi-delta {d_cls}">{m.get("delta")}</span>' if m.get("delta") else ""
                sub_h = f'<div class="custom-preview-kpi-subtext">{m.get("subtext")}</div>' if m.get("subtext") else ""
                cards.append(f"""
                <div class="custom-preview-kpi-card">
                    <div class="custom-preview-kpi-label">{m.get("label", "")}</div>
                    <div class="custom-preview-kpi-val-row">
                        <span class="custom-preview-kpi-val">{m.get("value", "")}</span>
                        {delta_h}
                    </div>
                    {sub_h}
                </div>
                """)
            cp_metrics_html = f'<div class="custom-preview-kpi-grid">{"".join(cards)}</div>'

        # Two-Column
        cp_two_col_html = ""
        tc = cp.get("two_column") or {}
        l_items = tc.get("left_items") or []
        r_items = tc.get("right_items") or []
        if l_items or r_items:
            l_lis = "".join([f'<li>{it}</li>' for it in l_items])
            r_lis = "".join([f'<li>{it}</li>' for it in r_items])
            cp_two_col_html = f"""
            <div class="custom-preview-two-col-grid">
                <div class="custom-preview-col-card left">
                    <div class="custom-preview-col-title">
                        <span class="col-indicator left"></span>
                        {tc.get("left_title", "Findings & Observations")}
                    </div>
                    <ul class="custom-preview-col-list">{l_lis}</ul>
                </div>
                <div class="custom-preview-col-card right">
                    <div class="custom-preview-col-title">
                        <span class="col-indicator right"></span>
                        {tc.get("right_title", "Recommended Action Plan")}
                    </div>
                    <ul class="custom-preview-col-list">{r_lis}</ul>
                </div>
            </div>
            """

        # Table
        cp_table_html = ""
        tbl = cp.get("table") or {}
        headers = tbl.get("headers") or []
        rows = tbl.get("rows") or []
        if headers:
            th_cells = "".join([f'<th>{h}</th>' for h in headers])
            tr_rows = []
            for r in rows:
                tds = "".join([f'<td class="{"text-left text-bold" if ci == 0 else ""}">{c}</td>' for ci, c in enumerate(r)])
                tr_rows.append(f'<tr>{tds}</tr>')
            fn_h = f'<div class="report-footnote">* {tbl.get("footnote")}</div>' if tbl.get("footnote") else ""
            cp_table_html = f"""
            <div style="margin-top: 16px; margin-bottom: 18px;">
                <table class="report-table">
                    <thead><tr>{th_cells}</tr></thead>
                    <tbody>{"".join(tr_rows)}</tbody>
                </table>
                {fn_h}
            </div>
            """

        # Paragraphs
        paras = cp.get("paragraphs") or []
        cp_paras_html = "".join([f'<p class="report-narrative" style="margin-top: 12px; margin-bottom: 16px;">{p}</p>' for p in paras])

        # Bullets
        bullets = cp.get("bullets") or []
        cp_bullets_html = f'<ul class="report-bullet-list" style="margin-top: 10px; margin-bottom: 18px;">{"".join([f"<li>{b}</li>" for b in bullets])}</ul>' if bullets else ""

        # Callout
        cp_callout_html = ""
        callout = cp.get("callout") or {}
        if callout.get("enabled") and callout.get("text"):
            c_type = callout.get("type", "highlight")
            c_title = callout.get("title", "Strategic Takeaway")
            c_text = callout.get("text", "")
            cp_callout_html = f"""
            <div class="custom-preview-callout-box {c_type}">
                <div class="custom-preview-callout-title">
                    <span>★ {c_title}</span>
                </div>
                <div class="custom-preview-callout-text">{c_text}</div>
            </div>
            """

        brand_header_html = f"""
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; border-bottom: 1px solid #f1f5f9; padding-bottom: 12px;">
            <div>{top_agency_logo}</div>
            <div style="text-align: right; display: flex; align-items: center; gap: 8px;">
                <span style="font-size: 11px; font-weight: 700; color: #64748b; letter-spacing: 0.04em; text-transform: uppercase;">
                    {company_name} • {curr_month_label}
                </span>
            </div>
        </div>
        """

        cp_body = f"""
            {brand_header_html}
            <h2 class="report-title-purple-left" style="margin-top: 10px; margin-bottom: 6px;">
                {cp_title}
            </h2>
            {cp_sub_html}
            {cp_intro_html}
            {cp_metrics_html}
            {cp_two_col_html}
            {cp_table_html}
            {cp_paras_html}
            {cp_bullets_html}
            {cp_callout_html}
        """
        custom_page_bodies.append(cp_body)

    standard_page_bodies = {
        1: f"""
            <div style="display: flex; justify-content: center; margin-top: 60px; margin-bottom: 60px;">
                {top_agency_logo}
            </div>

            <div style="margin: 25px 0;">
                <hr style="border: none; border-top: 1px solid #94a3b8; margin: 0 0 20px 0;" />
                <h2 style="font-size: 20px; font-weight: 700; color: #0f172a; text-align: center; margin: 0;">
                    {company_name} - Monthly SEO Report
                </h2>
                <hr style="border: none; border-top: 1px solid #94a3b8; margin: 20px 0 0 0;" />
            </div>

            <div style="flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 320px;">
                {hero_client_logo}
            </div>

            <div style="text-align: center; margin-bottom: 60px;">
                <p style="font-size: 15px; font-weight: 700; color: #0f172a; margin: 0 0 16px 0;">
                    SEO Report is from <span style="color: #7928ca;">{primary_period_long}</span>
                </p>
                <p style="font-size: 14px; color: #0f172a; margin: 0;">
                    Below are the performance metrics of current SEO report : <span style="color: #a855f7; font-weight: 700;">{company_name}</span>
                </p>
            </div>
        """,
        2: f"""
            <h2 class="report-title-purple-left" style="margin-top: 40px;">
                Key Improvements Achieved in {curr_month_label}
            </h2>
            <ul class="report-bullet-list" style="margin-top: 30px;">
                {key_improvements_html}
            </ul>
        """,
        3: f"""
            <h2 class="report-title-purple" style="margin-top: 40px; margin-bottom: 30px;">
                Upcoming SEO Focus
            </h2>
            <p class="report-narrative" style="margin-top: 0; margin-bottom: 24px;">
                {upcoming_focus_intro}
            </p>
            <ul class="report-bullet-list">
                {upcoming_focus_html}
            </ul>
        """,
        4: f"""
            <h2 class="report-title-purple" style="margin-top: 40px; margin-bottom: 35px;">
                Google Analytics
            </h2>
            <div class="report-subtitle-purple">
                <u>User Sessions Overview:</u>
            </div>
            {page_4_body}
        """,
        5: f"""
            <div class="report-subtitle-purple" style="margin-top: 40px; margin-bottom: 25px;">
                <u>Geographical Insights (Users by Country):</u>
            </div>
            {page_5_body}
        """,
        6: f"""
            <div class="report-subtitle-purple" style="margin-top: 40px; margin-bottom: 20px;">
                <u>User Engagement and Performance:</u>
            </div>
            {page_6_body}
            <p class="report-narrative">
                {p6_narrative}
            </p>
        """,
        7: f"""
            <h2 class="report-title-purple" style="text-align: center; margin-top: 35px; margin-bottom: 25px;">
                Google Search Console
            </h2>
            <div class="report-subtitle-purple" style="margin-bottom: 16px;">
                <u>GSC Overview: Clicks, Impressions & Position Trends:</u>
            </div>
            {page_7_body}
            <p class="report-narrative">
                {p7_narrative}
            </p>
        """,
        8: f"""
            {page_8_body}
            <p class="report-narrative" style="margin-top: 14px;">
                {p8_narrative}
            </p>
        """,
        9: f"""
            <h2 class="report-title-purple" style="margin-top: 40px; margin-bottom: 30px;">
                SEO Performance Overview
            </h2>
            <div class="report-subtitle-purple">
                <u>Semrush Organic Keyword</u>
            </div>
            {page_9_body}
            <p class="report-narrative" style="margin-top: 25px;">
                {p9_narrative}
            </p>
        """,
        10: f"""
            <div class="report-subtitle-purple" style="margin-top: 40px; margin-bottom: 25px;">
                <u>Branded And Non Branded</u>
            </div>
            {page_10_body}
            <p class="report-narrative" style="margin-top: 25px;">
                {p10_narrative}
            </p>
        """,
        11: f"""
            <div class="report-subtitle-purple" style="margin-top: 40px; margin-bottom: 25px;">
                <u>Crucial Metric</u>
            </div>
            {page_11_body}
            <p class="report-narrative" style="margin-top: 25px;">
                {p11_narrative}
            </p>
        """,
        12: f"""
            <h2 class="report-title-purple" style="margin-top: 40px; margin-bottom: 30px;">
                {company_name} : Off Page
            </h2>
            {page_12_body}
        """,
        13: f"""
            <h2 class="report-title-purple" style="margin-top: 40px; margin-bottom: 30px;">
                {company_name} – {curr_month_label} SEO Summary
            </h2>
            {seo_summary_html}
        """,
    }

    page_sequence = build_report_page_sequence(custom_pages)
    rendered_pages_list = []
    for item in page_sequence:
        p_num = item["resolved_page_num"]
        if item["type"] == "standard":
            p_content = standard_page_bodies[item["std_num"]]
        else:
            p_content = custom_page_bodies[item["orig_idx"]]
        rendered_pages_list.append(f"""
        <div class="report-page">
            {p_content}
            <div class="report-page-number">Page {p_num} of {total_pages}</div>
        </div>
        """)
    all_pages_html = "\n".join(rendered_pages_list)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{company_name} - Monthly SEO Report</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        @page {{
            size: A4 portrait;
            margin: 0;
        }}
        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}
        body {{
            font-family: 'Plus Jakarta Sans', system-ui, -apple-system, sans-serif;
            background-color: #f1f5f9;
            color: #111827;
            -webkit-print-color-adjust: exact;
            print-color-adjust: exact;
            padding: 24px 0;
        }}
        .report-page-container {{
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 28px;
        }}
        .report-page {{
            width: 210mm;
            min-height: 297mm;
            height: 297mm;
            background: #ffffff;
            color: #111827;
            padding: 22mm 22mm 16mm 22mm;
            box-sizing: border-box;
            position: relative;
            box-shadow: 0 8px 30px rgba(0,0,0,0.1);
            page-break-after: always;
            break-after: page;
            page-break-inside: avoid;
            display: flex;
            flex-direction: column;
        }}
        .report-page-number {{
            position: absolute;
            bottom: 15mm;
            right: 20mm;
            font-size: 11px;
            color: #94a3b8;
            font-weight: 500;
        }}
        .report-title-purple {{
            color: #8a2be2;
            font-size: 26px;
            font-weight: 700;
            text-align: center;
            margin-top: 10px;
            margin-bottom: 30px;
            letter-spacing: -0.01em;
        }}
        .report-title-purple-left {{
            color: #8a2be2;
            font-size: 26px;
            font-weight: 700;
            text-align: left;
            margin-top: 10px;
            margin-bottom: 30px;
            letter-spacing: -0.01em;
        }}
        .report-subtitle-purple {{
            color: #8a2be2;
            font-size: 16px;
            font-weight: 700;
            text-decoration: underline;
            margin-bottom: 20px;
        }}
        .report-narrative {{
            font-size: 13.5px;
            line-height: 1.65;
            color: #1f2937;
            margin-top: 20px;
        }}
        .report-bullet-list {{
            padding-left: 24px;
            margin-bottom: 20px;
        }}
        .report-bullet-list li {{
            font-size: 13.5px;
            line-height: 1.75;
            color: #111827;
            margin-bottom: 14px;
        }}
        .report-table {{
            width: 100%;
            border-collapse: collapse;
            border: 1px solid #000;
            margin-bottom: 16px;
            background: #ffffff;
        }}
        .report-table th,
        .report-table td {{
            border: 1px solid #000;
            padding: 6px 10px;
            font-size: 12.5px;
            text-align: center;
            color: #111827;
            line-height: 1.35;
        }}
        .report-table th {{
            font-weight: 700;
            background: #ffffff;
        }}
        .report-table td.text-left {{
            text-align: left;
        }}
        .report-table td.text-bold {{
            font-weight: 600;
        }}
        .report-footnote {{
            font-size: 12px;
            color: #111827;
            margin-top: 10px;
            margin-bottom: 16px;
        }}
        @media print {{
            body {{
                background: #ffffff !important;
                padding: 0 !important;
            }}
            .report-page-container {{
                gap: 0 !important;
            }}
            .report-page {{
                box-shadow: none !important;
                border: none !important;
                width: 210mm !important;
                height: 297mm !important;
                min-height: 297mm !important;
                margin: 0 !important;
            }}
            /* Custom Page Components */
            .custom-preview-kpi-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
                gap: 14px;
                margin-top: 14px;
                margin-bottom: 24px;
            }}
            .custom-preview-kpi-card {{
                background: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                padding: 14px 16px;
                display: flex;
                flex-direction: column;
                gap: 4px;
            }}
            .custom-preview-kpi-label {{
                font-size: 11px;
                font-weight: 700;
                color: #64748b;
                text-transform: uppercase;
                letter-spacing: 0.05em;
            }}
            .custom-preview-kpi-val-row {{
                display: flex;
                align-items: baseline;
                gap: 8px;
                margin-top: 2px;
            }}
            .custom-preview-kpi-val {{
                font-size: 26px;
                font-weight: 800;
                color: #0f172a;
                line-height: 1.1;
                font-family: system-ui, sans-serif;
            }}
            .custom-preview-kpi-delta {{
                font-size: 11.5px;
                font-weight: 700;
                padding: 1px 6px;
                border-radius: 4px;
            }}
            .custom-preview-kpi-delta.positive {{
                background: rgba(16, 185, 129, 0.12);
                color: #059669;
            }}
            .custom-preview-kpi-delta.negative {{
                background: rgba(239, 68, 68, 0.12);
                color: #dc2626;
            }}
            .custom-preview-kpi-subtext {{
                font-size: 11px;
                color: #94a3b8;
                margin-top: 2px;
            }}
            .custom-preview-two-col-grid {{
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 18px;
                margin-top: 14px;
                margin-bottom: 24px;
            }}
            .custom-preview-col-card {{
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                padding: 16px;
                background: #ffffff;
            }}
            .custom-preview-col-card.left {{
                border-left: 3px solid #8a2be2;
                background: #faf5ff;
            }}
            .custom-preview-col-card.right {{
                border-left: 3px solid #10b981;
                background: #f0fdf4;
            }}
            .custom-preview-col-title {{
                display: flex;
                align-items: center;
                gap: 8px;
                font-size: 13.5px;
                font-weight: 700;
                color: #0f172a;
                margin-bottom: 12px;
            }}
            .col-indicator {{
                width: 8px;
                height: 8px;
                border-radius: 50%;
                display: inline-block;
            }}
            .col-indicator.left {{
                background: #8a2be2;
            }}
            .col-indicator.right {{
                background: #10b981;
            }}
            .custom-preview-col-list {{
                list-style: none;
                padding: 0;
                margin: 0;
                display: flex;
                flex-direction: column;
                gap: 10px;
            }}
            .custom-preview-col-list li {{
                font-size: 12.5px;
                line-height: 1.55;
                color: #334155;
                position: relative;
                padding-left: 14px;
            }}
            .custom-preview-col-list li::before {{
                content: "•";
                position: absolute;
                left: 0;
                color: #64748b;
                font-weight: 700;
            }}
            .custom-preview-callout-box {{
                border-radius: 8px;
                padding: 16px 20px;
                margin-top: 20px;
                margin-bottom: 20px;
                display: flex;
                flex-direction: column;
                gap: 6px;
            }}
            .custom-preview-callout-box.highlight {{
                background: rgba(138, 43, 226, 0.06);
                border: 1px solid rgba(138, 43, 226, 0.25);
                border-left: 4px solid #8a2be2;
            }}
            .custom-preview-callout-box.success {{
                background: rgba(16, 185, 129, 0.06);
                border: 1px solid rgba(16, 185, 129, 0.25);
                border-left: 4px solid #10b981;
            }}
            .custom-preview-callout-box.info {{
                background: rgba(6, 182, 212, 0.06);
                border: 1px solid rgba(6, 182, 212, 0.25);
                border-left: 4px solid #06b6d4;
            }}
            .custom-preview-callout-box.warning {{
                background: rgba(245, 158, 11, 0.06);
                border: 1px solid rgba(245, 158, 11, 0.25);
                border-left: 4px solid #f59e0b;
            }}
            .custom-preview-callout-title {{
                font-size: 13px;
                font-weight: 700;
                color: #0f172a;
            }}
            .custom-preview-callout-box.highlight .custom-preview-callout-title {{
                color: #8a2be2;
            }}
            .custom-preview-callout-box.success .custom-preview-callout-title {{
                color: #059669;
            }}
            .custom-preview-callout-box.info .custom-preview-callout-title {{
                color: #0891b2;
            }}
            .custom-preview-callout-box.warning .custom-preview-callout-title {{
                color: #d97706;
            }}
            .custom-preview-callout-text {{
                font-size: 13px;
                line-height: 1.6;
                color: #334155;
            }}
        }}
    </style>
</head>
<body>
    <div class="report-page-container">
        {all_pages_html}
    </div>
</body>
</html>
"""

async def render_report_pdf(html_content: Union[str, dict]) -> str:
    """
    Renders standalone HTML to a vector-grade A4 PDF using Playwright Chromium.
    Returns the absolute path to the generated temporary PDF file.
    """
    if isinstance(html_content, dict):
        html_content = render_report_html(html_content)

    temp_html = tempfile.NamedTemporaryFile(delete=False, suffix=".html", mode="w", encoding="utf-8")
    temp_html.write(html_content)
    temp_html.close()

    temp_pdf = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    temp_pdf_name = temp_pdf.name
    temp_pdf.close()

    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
            )
            page = await browser.new_page()
            await page.goto(f"file://{temp_html.name}", wait_until="networkidle", timeout=30000)
            await page.pdf(
                path=temp_pdf_name,
                format="A4",
                print_background=True,
                margin={"top": "0", "right": "0", "bottom": "0", "left": "0"}
            )
            await browser.close()
    except Exception as e:
        print(f"Playwright PDF generation failed or not installed: {e}")
        raise RuntimeError(f"Playwright PDF generation error: {e}")
    finally:
        try:
            os.unlink(temp_html.name)
        except Exception:
            pass

    return temp_pdf_name
