import os
import re
import json
import secrets
import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from auth import get_current_user, get_optional_user, UserContext
from supabase_client import get_authenticated_client, get_supabase_client
from .report_aggregator import aggregate_monthly_seo_metrics, get_period_slice
from .report_llm import generate_monthly_seo_narratives
from .report_renderer import render_report_html, render_report_pdf

router = APIRouter(tags=["Monthly Reports"])

# Local persistent cache file in case Supabase schema migration hasn't been executed yet
FALLBACK_STORAGE_FILE = os.path.join(os.path.dirname(__file__), "reports_storage.json")

def load_local_reports() -> Dict[str, Dict[str, Any]]:
    if os.path.exists(FALLBACK_STORAGE_FILE):
        try:
            with open(FALLBACK_STORAGE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_local_reports(reports: Dict[str, Dict[str, Any]]):
    try:
        with open(FALLBACK_STORAGE_FILE, "w", encoding="utf-8") as f:
            json.dump(reports, f, indent=2)
    except Exception as e:
        print(f"Error saving local reports fallback: {e}")

class GenerateReportRequest(BaseModel):
    website: str
    month: Optional[str] = None # "YYYY-MM" (e.g. "2026-08") or "YYYY-MM-DD_to_YYYY-MM-DD"
    start_date: Optional[str] = None # "YYYY-MM-DD"
    end_date: Optional[str] = None # "YYYY-MM-DD"
    company_name: Optional[str] = None
    company_base_country: Optional[str] = None
    allow_sandbox_fallback: Optional[bool] = False
    openai_api_key: Optional[str] = None
    force: Optional[bool] = False

class AnalyzeReportRequest(BaseModel):
    openai_api_key: Optional[str] = None
    model: Optional[str] = None

class UpdateReportRequest(BaseModel):
    company_name: Optional[str] = None
    company_base_country: Optional[str] = None
    company_logo_url: Optional[str] = None
    period_label: Optional[str] = None
    agency_branding: Optional[Dict[str, Any]] = None
    narrative_data: Optional[Dict[str, Any]] = None
    metrics_data: Optional[Dict[str, Any]] = None

# ----------------------------------------------------------------------
# Authenticated Endpoints: /api/monthly-reports
# ----------------------------------------------------------------------

@router.get("/api/monthly-reports")
async def list_monthly_reports(
    website: Optional[str] = None,
    user: UserContext = Depends(get_current_user)
):
    """Lists all monthly reports for the authenticated user."""
    client = get_authenticated_client(user.access_token)
    reports_list = []

    # 1. Attempt Supabase query
    try:
        query = client.table("monthly_reports").select("*").eq("user_id", user.id)
        if website:
            clean_w = re.sub(r"^https?://", "", website.strip().lower()).split("/")[0].split("?")[0]
            domain_variants = list(set([website, website.strip(), clean_w, f"https://{clean_w}", f"http://{clean_w}"]))
            query = query.in_("website", domain_variants)
        res = query.order("created_at", desc=True).execute()
        if res.data:
            reports_list = res.data
        elif res.data is not None and len(res.data) == 0:
            # Table exists in Supabase but user has no records yet.
            # Seamlessly sync historical reports from local storage to Supabase
            local_db = load_local_reports()
            user_reports = [r for r in local_db.values() if r.get("user_id") == user.id]
            if user_reports:
                for r in user_reports:
                    try:
                        client.table("monthly_reports").upsert(r).execute()
                    except Exception as upsert_err:
                        print(f"Notice: Auto-migration upsert error: {upsert_err}")
                # Re-query Supabase
                refreshed = query.order("created_at", desc=True).execute()
                if refreshed.data:
                    reports_list = refreshed.data
    except Exception as e:
        print(f"Notice: Supabase query failed (possibly unmigrated table): {e}, checking local storage.")
        # Fallback to local storage
        local_db = load_local_reports()
        for r_id, r in local_db.items():
            if r.get("user_id") == user.id:
                if not website or r.get("website") == website:
                    reports_list.append(r)
        reports_list.sort(key=lambda x: x.get("created_at", ""), reverse=True)

    # Format quick response items
    formatted = []
    for r in reports_list:
        metrics = r.get("metrics_data", {})
        traffic = metrics.get("section_01_website_traffic", {})
        strip = traffic.get("stat_strip", [])
        gsc = metrics.get("section_02_google_search", {})
        gsc_strip = gsc.get("stat_strip", [])
        
        users_val = strip[0].get("value") if len(strip) > 0 and strip[0].get("value") else "No data available"
        users_delta = strip[0].get("delta", "") if len(strip) > 0 else ""
        clicks_val = gsc_strip[0].get("value") if len(gsc_strip) > 0 and gsc_strip[0].get("value") else "No data available"
        clicks_delta = gsc_strip[0].get("delta", "") if len(gsc_strip) > 0 else ""

        meta = metrics.get("metadata", {})
        raw_period = r.get("period_label") or meta.get("period_label") or ""
        primary_label = (
            r.get("primary_period_label")
            or meta.get("primary_period_label")
            or (raw_period.split(" (vs ")[0] if " (vs " in raw_period else (raw_period.split(",")[0] if "," in raw_period else raw_period))
        )
        comp_label = (
            r.get("comparison_period_label")
            or meta.get("comparison_period_label")
            or (raw_period.split(" (vs ")[1].rstrip(")") if " (vs " in raw_period else (raw_period.split("compared against")[1].strip() if "compared against" in raw_period else None))
        )

        formatted.append({
            "id": r.get("id"),
            "website": r.get("website"),
            "company_name": r.get("company_name"),
            "report_month": r.get("report_month"),
            "curr_month_label": meta.get("curr_month_label"),
            "period_label": raw_period,
            "primary_period_label": primary_label,
            "comparison_period_label": comp_label,
            "status": r.get("status", "draft"),
            "share_token": r.get("share_token"),
            "agency_branding": r.get("agency_branding", {}),
            "created_at": r.get("created_at"),
            "summary_highlights": {
                "total_users": users_val,
                "users_delta": users_delta,
                "total_clicks": clicks_val,
                "clicks_delta": clicks_delta
            }
        })

    return formatted

@router.post("/api/monthly-reports/generate")
async def generate_monthly_report(
    payload: GenerateReportRequest,
    user: UserContext = Depends(get_current_user)
):
    """
    Generates a new Monthly SEO Report in 'draft' status.
    Enforces:
    - Pre-flight connection verification (GA4 & GSC required)
    - Strict single generation per website per calendar month
    """
    client = get_authenticated_client(user.access_token)
    website = payload.website.strip()
    month = payload.month.strip() if payload.month else None
    start_date = payload.start_date.strip() if payload.start_date else None
    end_date = payload.end_date.strip() if payload.end_date else None

    if not month and not (start_date and end_date):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please provide a calendar month or custom start and end dates."
        )

    if start_date and end_date:
        try:
            s_d = datetime.strptime(start_date, "%Y-%m-%d").date()
            e_d = datetime.strptime(end_date, "%Y-%m-%d").date()
            if e_d < s_d:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="End date cannot be earlier than start date."
                )
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid date format. Expected YYYY-MM-DD."
            )
        report_month_key = f"{start_date}_to_{end_date}"
    else:
        report_month_key = month

    # 1. Fetch user's integrations
    integration_rows = []
    try:
        res = client.table("integrations").select("*").eq("user_id", user.id).execute()
        integration_rows = res.data or []
    except Exception as e:
        print(f"Notice: Could not load user integrations: {e}")

    # 1b. Check if client record has dedicated GA4 / GSC mappings for this website
    clean_domain = website.replace("https://", "").replace("http://", "").split("/")[0].lower()
    client_rec = None
    try:
        c_res = client.table("clients").select("*").eq("website", clean_domain).eq("user_id", user.id).execute()
        if not c_res.data:
            c_res = client.table("clients").select("*").eq("website", website).eq("user_id", user.id).execute()
        client_rec = c_res.data[0] if (c_res.data and len(c_res.data) > 0) else None
        if not client_rec:
            from routers.clients import load_local_clients
            local_store = load_local_clients()
            for c in local_store.get(user.id, []):
                c_clean = (c.get("website") or "").replace("https://", "").replace("http://", "").split("/")[0].lower()
                if c_clean == clean_domain or c.get("website") == website:
                    client_rec = c
                    break

        if client_rec:
            c_ga4 = client_rec.get("ga4_property_id")
            c_gsc = client_rec.get("gsc_site_url")
            if c_ga4:
                found_ga = False
                for row in integration_rows:
                    if row.get("name") == "google-analytics":
                        row["selected_property"] = c_ga4
                        found_ga = True
                        break
                if not found_ga:
                    integration_rows.append({"name": "google-analytics", "is_connected": True, "selected_property": c_ga4})
            if c_gsc:
                found_gsc = False
                for row in integration_rows:
                    if row.get("name") == "search-console":
                        row["selected_property"] = c_gsc
                        found_gsc = True
                        break
                if not found_gsc:
                    integration_rows.append({"name": "search-console", "is_connected": True, "selected_property": c_gsc})
    except Exception as ex:
        print(f"Notice: Could not load client-specific mappings: {ex}")

    # 2. Check for duplicate report for this period
    existing_report = None
    try:
        dup = client.table("monthly_reports").select("id, status").eq("user_id", user.id).eq("website", website).eq("report_month", report_month_key).execute()
        if dup.data and len(dup.data) > 0:
            existing_report = dup.data[0]
    except Exception:
        local_db = load_local_reports()
        for r_id, r in local_db.items():
            if r.get("user_id") == user.id and r.get("website") == website and r.get("report_month") == report_month_key:
                existing_report = r
                break

    if existing_report:
        if payload.force:
            old_id = existing_report.get("id")
            try:
                client.table("monthly_reports").delete().eq("id", old_id).eq("user_id", user.id).execute()
            except Exception:
                pass
            local_db = load_local_reports()
            if old_id in local_db:
                del local_db[old_id]
                save_local_reports(local_db)
        else:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": f"An SEO report for {website} for {report_month_key} has already been generated.",
                    "existing_id": existing_report.get("id"),
                    "status": existing_report.get("status")
                }
            )

    # 3. Data Aggregation Pipeline (Validates GA4/GSC connections)
    metrics_data = await aggregate_monthly_seo_metrics(
        user_id=user.id,
        website=website,
        month_str=month,
        company_name=payload.company_name,
        integrations_rows=integration_rows,
        user=user,
        client_rec=client_rec,
        start_date=start_date,
        end_date=end_date
    )

    # 4. Factual AI Narrative Generation (or deterministic fallback)
    narrative_data = await generate_monthly_seo_narratives(
        metrics_data,
        openai_api_key=payload.openai_api_key
    )

    report_id = str(uuid.uuid4())
    now_str = datetime.utcnow().isoformat() + "Z"
    meta = metrics_data.get("metadata", {})
    comp_name = payload.company_name or meta.get("company_name", website)

    record = {
        "id": report_id,
        "user_id": user.id,
        "website": website,
        "company_name": comp_name,
        "company_base_country": meta.get("company_base_country") or (client_rec.get("company_base_country") if client_rec else None),
        "report_month": report_month_key,
        "period_label": meta.get("period_label", f"Report {report_month_key}"),
        "primary_period_label": meta.get("primary_period_label"),
        "comparison_period_label": meta.get("comparison_period_label"),
        "status": "draft",
        "share_token": None,
        "agency_branding": {
            "agency_name": "SEO Reporter",
            "accent_color": "#6366f1",
            "logo_url": ""
        },
        "metrics_data": metrics_data,
        "narrative_data": narrative_data,
        "created_at": now_str,
        "updated_at": now_str
    }

    # 5. Persist directly to Supabase as primary database
    saved_to_supabase = False
    try:
        res = client.table("monthly_reports").insert(record).execute()
        if res.data:
            saved_to_supabase = True
    except Exception as e:
        print(f"Notice: Supabase insert failed: {e}, saving to fallback local storage.")

    # Only write to local storage if Supabase failed (e.g. unmigrated table)
    if not saved_to_supabase:
        local_db = load_local_reports()
        local_db[report_id] = record
        save_local_reports(local_db)

    return record

@router.get("/api/monthly-reports/{report_id}")
async def get_monthly_report(
    report_id: str,
    user: UserContext = Depends(get_current_user)
):
    """Retrieves single report by ID."""
    client = get_authenticated_client(user.access_token)
    try:
        res = client.table("monthly_reports").select("*").eq("id", report_id).eq("user_id", user.id).execute()
        if res.data and len(res.data) > 0:
            return res.data[0]
    except Exception:
        pass

    local_db = load_local_reports()
    if report_id in local_db:
        r = local_db[report_id]
        if r.get("user_id") == user.id:
            try:
                client.table("monthly_reports").upsert(r).execute()
            except Exception:
                pass
            return r

    raise HTTPException(status_code=404, detail="Monthly report not found.")

@router.put("/api/monthly-reports/{report_id}")
async def update_monthly_report(
    report_id: str,
    payload: UpdateReportRequest,
    user: UserContext = Depends(get_current_user)
):
    """
    Updates editable fields of a draft or published report.
    Hard metrics data remains immutable.
    """
    client = get_authenticated_client(user.access_token)
    now_str = datetime.utcnow().isoformat() + "Z"

    # Fetch existing
    existing = None
    try:
        res = client.table("monthly_reports").select("*").eq("id", report_id).eq("user_id", user.id).execute()
        if res.data and len(res.data) > 0:
            existing = res.data[0]
    except Exception:
        pass

    if not existing:
        local_db = load_local_reports()
        if report_id in local_db and local_db[report_id].get("user_id") == user.id:
            existing = local_db[report_id]

    if not existing:
        raise HTTPException(status_code=404, detail="Monthly report not found.")

    update_fields = {"updated_at": now_str}
    if payload.company_name is not None:
        update_fields["company_name"] = payload.company_name
    if payload.company_base_country is not None:
        update_fields["company_base_country"] = payload.company_base_country
    if payload.period_label is not None:
        update_fields["period_label"] = payload.period_label
    if payload.agency_branding is not None:
        branding = existing.get("agency_branding", {}) or {}
        branding.update(payload.agency_branding)
        if payload.company_logo_url is not None:
            branding["company_logo_url"] = payload.company_logo_url
        if payload.company_base_country is not None:
            branding["company_base_country"] = payload.company_base_country
        update_fields["agency_branding"] = branding
    if payload.narrative_data is not None:
        narrative = existing.get("narrative_data", {}) or {}
        narrative.update(payload.narrative_data)
        if payload.company_logo_url is not None:
            narrative["company_logo_url"] = payload.company_logo_url
        if payload.company_base_country is not None:
            narrative["company_base_country"] = payload.company_base_country
        update_fields["narrative_data"] = narrative
    elif payload.company_base_country is not None:
        narrative = existing.get("narrative_data", {}) or {}
        narrative["company_base_country"] = payload.company_base_country
        update_fields["narrative_data"] = narrative
    if payload.metrics_data is not None:
        metrics_dict = existing.get("metrics_data", {}) or {}
        for k, v in payload.metrics_data.items():
            if isinstance(v, dict) and isinstance(metrics_dict.get(k), dict):
                metrics_dict[k].update(v)
            else:
                metrics_dict[k] = v
        update_fields["metrics_data"] = metrics_dict

    # Apply to Supabase as primary database
    updated_in_supabase = False
    try:
        res = client.table("monthly_reports").update(update_fields).eq("id", report_id).eq("user_id", user.id).execute()
        if res.data:
            updated_in_supabase = True
    except Exception as e:
        print(f"Notice: Supabase update error: {e}")

    # Only update local storage if Supabase failed or was unmigrated
    if not updated_in_supabase:
        local_db = load_local_reports()
        if report_id in local_db:
            local_db[report_id].update(update_fields)
            save_local_reports(local_db)

    existing.update(update_fields)
    return existing

@router.post("/api/monthly-reports/{report_id}/analyze")
async def analyze_monthly_report_content(
    report_id: str,
    payload: Optional[AnalyzeReportRequest] = None,
    user: UserContext = Depends(get_current_user)
):
    """
    Synthesizes zero-hallucination executive narratives from real report metrics
    using ChatGPT API (or deterministic engine if unconfigured).
    Updates narrative_data in place and returns the updated report.
    """
    client = get_authenticated_client(user.access_token)
    now_str = datetime.utcnow().isoformat() + "Z"

    # 1. Fetch existing report
    existing = None
    try:
        res = client.table("monthly_reports").select("*").eq("id", report_id).eq("user_id", user.id).execute()
        if res.data and len(res.data) > 0:
            existing = res.data[0]
    except Exception:
        pass

    if not existing:
        local_db = load_local_reports()
        if report_id in local_db and local_db[report_id].get("user_id") == user.id:
            existing = local_db[report_id]

    if not existing:
        raise HTTPException(status_code=404, detail="Monthly report not found.")

    metrics_data = existing.get("metrics_data", {})
    api_key = payload.openai_api_key if payload else None
    model = payload.model if payload else None

    # 2. Run ChatGPT analysis with strict grounding
    narrative_data = await generate_monthly_seo_narratives(
        metrics_data,
        openai_api_key=api_key,
        model=model
    )

    # 3. Update report record
    update_fields = {
        "narrative_data": narrative_data,
        "updated_at": now_str
    }

    analyzed_in_supabase = False
    try:
        res = client.table("monthly_reports").update(update_fields).eq("id", report_id).eq("user_id", user.id).execute()
        if res.data:
            analyzed_in_supabase = True
    except Exception as e:
        print(f"Notice: Supabase update error during analyze: {e}")

    if not analyzed_in_supabase:
        local_db = load_local_reports()
        if report_id in local_db:
            local_db[report_id].update(update_fields)
            save_local_reports(local_db)

    existing.update(update_fields)
    return existing

@router.post("/api/monthly-reports/{report_id}/publish")
async def publish_monthly_report(
    report_id: str,
    user: UserContext = Depends(get_current_user)
):
    """
    Publishes the report, issues an unlisted high-entropy share token,
    making it accessible to external clients via the public link.
    """
    client = get_authenticated_client(user.access_token)
    share_token = secrets.token_urlsafe(24)
    now_str = datetime.utcnow().isoformat() + "Z"

    update_fields = {
        "status": "published",
        "share_token": share_token,
        "updated_at": now_str
    }

    try:
        res = client.table("monthly_reports").update(update_fields).eq("id", report_id).eq("user_id", user.id).execute()
        if res.data and len(res.data) > 0:
            return res.data[0]
    except Exception as e:
        print(f"Notice: Supabase publish error: {e}")

    local_db = load_local_reports()
    if report_id in local_db:
        local_db[report_id].update(update_fields)
        save_local_reports(local_db)
        return local_db[report_id]

    raise HTTPException(status_code=404, detail="Report not found")

@router.delete("/api/monthly-reports/{report_id}")
async def delete_monthly_report(
    report_id: str,
    user: UserContext = Depends(get_current_user)
):
    """
    Deletes a monthly SEO report belonging to the authenticated user from Supabase and local cache.
    Returns: Success message confirming report deletion.
    """
    client = get_authenticated_client(user.access_token)
    try:
        client.table("monthly_reports").delete().eq("id", report_id).eq("user_id", user.id).execute()
    except Exception as e:
        print(f"Notice: Supabase delete error: {e}")

    local_db = load_local_reports()
    if report_id in local_db:
        del local_db[report_id]
        save_local_reports(local_db)

    return {"success": True, "message": "Report deleted successfully."}

@router.get("/api/monthly-reports/{report_id}/html")
async def get_monthly_report_html(
    report_id: str,
    user: UserContext = Depends(get_current_user)
):
    """
    Renders the complete 13-page report as a self-contained, standalone HTML document.
    Returns: HTML document response suitable for in-browser printing or offline viewing.
    """
    report = await get_monthly_report(report_id, user)
    html_content = render_report_html(report)
    return HTMLResponse(content=html_content)

@router.get("/api/monthly-reports/{report_id}/pdf")
async def get_monthly_report_pdf(
    report_id: str,
    user: UserContext = Depends(get_current_user)
):
    """
    Renders and streams a vector-grade, print-ready A4 PDF document generated via headless Chromium.
    Returns: Binary application/pdf file stream with attachment disposition header.
    """
    report = await get_monthly_report(report_id, user)
    html_content = render_report_html(report)
    
    try:
        pdf_path = await render_report_pdf(html_content)
        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()
        try:
            os.unlink(pdf_path)
        except Exception:
            pass

        filename = f"monthly_seo_report_{report.get('report_month', 'report')}.pdf"
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate PDF: {str(e)}"
        )

# ----------------------------------------------------------------------
# Public Unauthenticated Endpoints: /api/public/reports
# ----------------------------------------------------------------------

@router.get("/api/public/reports/{share_token}")
async def get_public_report(share_token: str):
    """
    Public unauthenticated endpoint to fetch a published report by its unlisted share token.
    Subject to Supabase RLS public SELECT policy for published reports.
    Returns: Published report JSON record or raises 404.
    """
    client = get_supabase_client()
    try:
        res = client.table("monthly_reports").select("*").eq("share_token", share_token).eq("status", "published").execute()
        if res.data and len(res.data) > 0:
            return res.data[0]
    except Exception:
        pass

    local_db = load_local_reports()
    for r_id, r in local_db.items():
        if r.get("share_token") == share_token and r.get("status") == "published":
            return r

    raise HTTPException(status_code=404, detail="Shared monthly report not found or is no longer published.")

@router.get("/api/public/reports/{share_token}/html")
async def get_public_report_html(share_token: str):
    """
    Public unauthenticated endpoint to render a published report as a standalone HTML document.
    Returns: Full HTML document response for client viewing.
    """
    report = await get_public_report(share_token)
    html_content = render_report_html(report)
    return HTMLResponse(content=html_content)

@router.get("/api/public/reports/{share_token}/pdf")
async def get_public_report_pdf(share_token: str):
    """
    Public unauthenticated endpoint to download a published report as a vector-grade A4 PDF.
    Returns: Binary application/pdf file stream with attachment disposition header.
    """
    report = await get_public_report(share_token)
    html_content = render_report_html(report)

    try:
        pdf_path = await render_report_pdf(html_content)
        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()
        try:
            os.unlink(pdf_path)
        except Exception:
            pass

        filename = f"monthly_seo_report_{report.get('report_month', 'report')}.pdf"
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate PDF: {str(e)}"
        )
