from fastapi import APIRouter
from config import settings
import httpx

router = APIRouter(prefix="/api", tags=["Health"])

@router.get("/health")
async def health_check():
    """
    Health check endpoint.
    Tests backend server availability and verifies connection to the Supabase database.
    Returns: Service health status and Supabase connectivity status.
    """
    supabase_status = "unknown"
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get(
                f"{settings.SUPABASE_URL}/auth/v1/settings",
                headers={"apikey": settings.SUPABASE_KEY}
            )
            if resp.status_code == 200:
                supabase_status = "connected"
            else:
                supabase_status = f"error_{resp.status_code}"
    except Exception as e:
        supabase_status = f"unreachable: {str(e)}"

    return {
        "status": "healthy",
        "service": "SEO Reporter Backend",
        "version": "1.0.0",
        "supabase": {
            "url": settings.SUPABASE_URL,
            "status": supabase_status
        }
    }
