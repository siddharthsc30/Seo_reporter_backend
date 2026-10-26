from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from config import settings
from routers import health, auth, integrations, reports, analytics, clients
from modules.monthly_reports import report_routes

app = FastAPI(
    title="SEO Reporter Backend",
    description="Full-functioning API for SEO Report Automation integrated with Supabase PostgreSQL & Authentication",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Configure Cross-Origin Resource Sharing (CORS)
# If ALLOWED_ORIGINS is configured, use it directly. Always ensure localhost is permitted in development.
effective_origins = list(settings.ALLOWED_ORIGINS) if settings.ALLOWED_ORIGINS else []
for default_dev in ["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000"]:
    if default_dev not in effective_origins:
        effective_origins.append(default_dev)

app.add_middleware(
    CORSMiddleware,
    allow_origins=effective_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register Routers
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(clients.router)
app.include_router(integrations.router)
app.include_router(reports.router)
app.include_router(analytics.router)
app.include_router(report_routes.router)

@app.get("/")
async def root():
    """
    Root status endpoint. Returns API service name, documentation URL, and operational health status.
    """
    return {
        "service": "SEO Reporter API",
        "docs": "/docs",
        "status": "operational",
        "supabaseUrl": settings.SUPABASE_URL
    }

@app.on_event("shutdown")
async def shutdown_event():
    from supabase_client import close_supabase_http_client
    await close_supabase_http_client()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=settings.HOST, port=settings.PORT, reload=True)
