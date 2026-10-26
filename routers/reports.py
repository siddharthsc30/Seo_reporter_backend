from typing import Optional, List, Dict, Any
from datetime import datetime
import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from auth import get_current_user, get_optional_user, UserContext
from supabase_client import get_authenticated_client

router = APIRouter(prefix="/api/reports", tags=["Reports"])

class SaveReportRequest(BaseModel):
    id: Optional[str] = None
    name: str
    domain: str
    dateRangeLabel: str = "Last 30 Days"
    sources: List[str] = Field(default_factory=list)
    config: Dict[str, Any] = Field(default_factory=dict)
    dataSnapshot: Dict[str, Any] = Field(default_factory=dict)

@router.get("")
async def list_reports(user: Optional[UserContext] = Depends(get_optional_user)):
    """
    Returns list of saved SEO reports for the authenticated user from Supabase.
    Unifies records from monthly_reports (primary) and saved_reports (legacy) tables.
    If not logged in, returns empty list.
    """
    if not user:
        return []

    client = get_authenticated_client(user.access_token)
    formatted_reports = []

    # 1. Fetch from monthly_reports (Active Monthly SEO Report Engine)
    try:
        m_res = client.table("monthly_reports").select("*").eq("user_id", user.id).order("created_at", desc=True).execute()
        m_rows = m_res.data or []
        for r in m_rows:
            metrics = r.get("metrics_data") or {}
            traffic = metrics.get("section_01_website_traffic", {})
            strip = traffic.get("stat_strip", [])
            gsc = metrics.get("section_02_google_search", {})
            gsc_strip = gsc.get("stat_strip", [])

            users_val = strip[0].get("value") if len(strip) > 0 and strip[0].get("value") else 0
            clicks_val = gsc_strip[0].get("value") if len(gsc_strip) > 0 and gsc_strip[0].get("value") else 0

            try:
                if isinstance(users_val, str):
                    users_num = int(users_val.replace(",", "").replace("+", "").strip())
                else:
                    users_num = int(users_val)
            except Exception:
                users_num = 0

            try:
                if isinstance(clicks_val, str):
                    clicks_num = int(clicks_val.replace(",", "").replace("+", "").strip())
                else:
                    clicks_num = int(clicks_val)
            except Exception:
                clicks_num = 0

            comp_name = r.get("company_name") or r.get("website") or "Client"
            raw_period = r.get("period_label") or r.get("report_month") or "Monthly Report"
            primary_label = r.get("primary_period_label") or raw_period.split(" (vs ")[0].split(", compared against ")[0]

            formatted_reports.append({
                "id": str(r.get("id")),
                "name": f"{comp_name} - {primary_label}",
                "domain": r.get("website") or "",
                "companyName": comp_name,
                "dateRangeLabel": raw_period,
                "primaryPeriodLabel": primary_label,
                "comparisonPeriodLabel": r.get("comparison_period_label"),
                "generatedAt": str(r.get("created_at", ""))[:10],
                "status": r.get("status", "draft"),
                "shareToken": r.get("share_token"),
                "isMonthlyReport": True,
                "sources": ["google-analytics", "search-console", "semrush"],
                "config": {
                    "dateRange": r.get("report_month"),
                    "name": comp_name,
                    "domain": r.get("website") or "",
                },
                "dataSnapshot": {
                    "googleAnalytics": {"sessions": users_num},
                    "searchConsole": {"clicks": clicks_num},
                    "semrush": {"authorityScore": 10}
                },
                "created_at": r.get("created_at", "")
            })
    except Exception as e:
        print(f"Error fetching monthly_reports in list_reports: {e}")

    # 2. Fetch from saved_reports (Legacy archives)
    try:
        s_res = client.table("saved_reports").select("*").eq("user_id", user.id).order("created_at", desc=True).execute()
        s_rows = s_res.data or []
        for r in s_rows:
            snapshot = r.get("data_snapshot") or {}
            formatted_reports.append({
                "id": str(r.get("id")),
                "name": r.get("name") or "SEO Performance Report",
                "domain": r.get("domain") or "",
                "companyName": r.get("name") or r.get("domain") or "",
                "dateRangeLabel": r.get("date_range_label") or "Last 30 Days",
                "primaryPeriodLabel": r.get("date_range_label") or "Last 30 Days",
                "comparisonPeriodLabel": None,
                "generatedAt": str(r.get("created_at", ""))[:10],
                "status": "archived",
                "shareToken": None,
                "isMonthlyReport": False,
                "sources": snapshot.get("sources", ["google-analytics", "search-console"]),
                "config": snapshot.get("config", {}),
                "dataSnapshot": snapshot.get("dataSnapshot") or snapshot.get("data") or {},
                "created_at": r.get("created_at", "")
            })
    except Exception as e:
        print(f"Error fetching saved_reports in list_reports: {e}")

    # Sort descending by created_at
    formatted_reports.sort(key=lambda x: str(x.get("created_at", "")), reverse=True)
    return formatted_reports

@router.post("")
async def save_report(
    payload: SaveReportRequest,
    user: UserContext = Depends(get_current_user)
):
    """
    Saves a generated SEO report snapshot into Supabase table saved_reports.
    """
    client = get_authenticated_client(user.access_token)
    report_id = payload.id or str(uuid.uuid4())
    now_str = datetime.utcnow().isoformat()

    snapshot_payload = {
        "sources": payload.sources,
        "config": payload.config,
        "dataSnapshot": payload.dataSnapshot
    }

    record = {
        "id": report_id,
        "user_id": user.id,
        "domain": payload.domain,
        "name": payload.name,
        "date_range_label": payload.dateRangeLabel,
        "created_at": now_str,
        "data_snapshot": snapshot_payload
    }

    try:
        res = client.table("saved_reports").insert(record).execute()
        return {
            "success": True,
            "id": report_id,
            "message": "Report saved successfully to Supabase",
            "report": {
                "id": report_id,
                "name": payload.name,
                "domain": payload.domain,
                "dateRangeLabel": payload.dateRangeLabel,
                "generatedAt": now_str[:10],
                "sources": payload.sources,
                "config": payload.config,
                "dataSnapshot": payload.dataSnapshot
            }
        }
    except Exception as e:
        print(f"Error creating report in Supabase: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error saving report: {str(e)}"
        )

@router.get("/{report_id}")
async def get_report(
    report_id: str,
    user: UserContext = Depends(get_current_user)
):
    """Retrieves a single saved report by ID from Supabase (checking monthly_reports then saved_reports)."""
    client = get_authenticated_client(user.access_token)
    # Check monthly_reports first
    try:
        m_res = client.table("monthly_reports").select("*").eq("id", report_id).eq("user_id", user.id).execute()
        if m_res.data and len(m_res.data) > 0:
            r = m_res.data[0]
            metrics = r.get("metrics_data") or {}
            traffic = metrics.get("section_01_website_traffic", {})
            strip = traffic.get("stat_strip", [])
            gsc = metrics.get("section_02_google_search", {})
            gsc_strip = gsc.get("stat_strip", [])
            users_val = strip[0].get("value") if len(strip) > 0 and strip[0].get("value") else 0
            clicks_val = gsc_strip[0].get("value") if len(gsc_strip) > 0 and gsc_strip[0].get("value") else 0
            try:
                users_num = int(str(users_val).replace(",", "").replace("+", "").strip())
            except Exception:
                users_num = 0
            try:
                clicks_num = int(str(clicks_val).replace(",", "").replace("+", "").strip())
            except Exception:
                clicks_num = 0

            comp_name = r.get("company_name") or r.get("website") or "Client"
            raw_period = r.get("period_label") or r.get("report_month") or "Monthly Report"
            primary_label = r.get("primary_period_label") or raw_period.split(" (vs ")[0].split(", compared against ")[0]

            return {
                "id": str(r.get("id")),
                "name": f"{comp_name} - {primary_label}",
                "domain": r.get("website") or "",
                "companyName": comp_name,
                "dateRangeLabel": raw_period,
                "primaryPeriodLabel": primary_label,
                "comparisonPeriodLabel": r.get("comparison_period_label"),
                "generatedAt": str(r.get("created_at", ""))[:10],
                "status": r.get("status", "draft"),
                "shareToken": r.get("share_token"),
                "isMonthlyReport": True,
                "sources": ["google-analytics", "search-console", "semrush"],
                "config": {
                    "dateRange": r.get("report_month"),
                    "name": comp_name,
                    "domain": r.get("website") or "",
                },
                "dataSnapshot": {
                    "googleAnalytics": {"sessions": users_num},
                    "searchConsole": {"clicks": clicks_num},
                    "semrush": {"authorityScore": 10}
                }
            }
    except Exception as e:
        print(f"Error checking monthly_reports for {report_id}: {e}")

    # Check saved_reports table
    try:
        res = client.table("saved_reports").select("*").eq("id", report_id).eq("user_id", user.id).execute()
        if not res.data or len(res.data) == 0:
            raise HTTPException(status_code=404, detail="Report not found")
        
        r = res.data[0]
        snapshot = r.get("data_snapshot") or {}
        return {
            "id": str(r.get("id")),
            "name": r.get("name"),
            "domain": r.get("domain"),
            "companyName": r.get("name") or r.get("domain") or "",
            "dateRangeLabel": r.get("date_range_label"),
            "primaryPeriodLabel": r.get("date_range_label"),
            "comparisonPeriodLabel": None,
            "generatedAt": str(r.get("created_at", ""))[:10],
            "status": "archived",
            "shareToken": None,
            "isMonthlyReport": False,
            "sources": snapshot.get("sources", []),
            "config": snapshot.get("config", {}),
            "dataSnapshot": snapshot.get("dataSnapshot", {})
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/{report_id}")
async def delete_report(
    report_id: str,
    user: UserContext = Depends(get_current_user)
):
    """Deletes a saved report from Supabase (deleting from monthly_reports and saved_reports)."""
    client = get_authenticated_client(user.access_token)
    try:
        # Delete from monthly_reports
        try:
            client.table("monthly_reports").delete().eq("id", report_id).eq("user_id", user.id).execute()
        except Exception as m_err:
            print(f"Notice: monthly_reports delete: {m_err}")

        # Delete from saved_reports
        try:
            client.table("saved_reports").delete().eq("id", report_id).eq("user_id", user.id).execute()
        except Exception as s_err:
            print(f"Notice: saved_reports delete: {s_err}")

        # Also purge from local fallback storage if present
        try:
            from modules.monthly_reports.report_routes import load_local_reports, save_local_reports
            local_db = load_local_reports()
            if report_id in local_db:
                del local_db[report_id]
                save_local_reports(local_db)
        except Exception:
            pass

        return {
            "success": True,
            "message": "Report deleted successfully"
        }
    except Exception as e:
        print(f"Error deleting report: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete report: {str(e)}"
        )
