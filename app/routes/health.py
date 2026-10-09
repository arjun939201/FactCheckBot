from fastapi import APIRouter
from fastapi.responses import JSONResponse
from ..config import get_settings
from ..services.groq import get_ai_status
from .history import store

router = APIRouter()

@router.get("/health")
def health():
    s=get_settings()
    return {"status":"ok","service":s.app_name,"version":s.app_version,"environment":s.app_env}

@router.get("/health/readiness")
def readiness():
    s=get_settings()
    try:
        store.ping()
        database_ok=True
    except Exception:
        database_ok=False
    checks={"database":database_ok,"ai":bool(s.groq_api_key),"search":True}
    ready=all(checks.values())
    return JSONResponse(
        {"status":"ready" if ready else "degraded","checks":checks,"version":s.app_version},
        status_code=200 if ready else 503,
    )

@router.get("/capabilities")
def capabilities():
    s=get_settings()
    return {"text_research":bool(s.groq_api_key),"article_research":True,"web_search":True,"history":True,"chat":bool(s.groq_api_key),"media_upload":True,"media_visual_analysis":"dynamic","transcription":bool(s.groq_api_key)}


@router.get("/ai-status")
def ai_status():
    """Report the last observed AI state and any provider-reported cooldown."""
    s = get_settings()
    status = get_ai_status()
    if not s.groq_api_key:
        status.update({"state": "unavailable", "detail": "AI API key is not configured", "retry_after_seconds": 0})
    elif status.get("state") == "unknown":
        # A fresh worker has no request history yet. Distinguish configured-but-
        # untested from a broken status endpoint; do not claim provider health.
        status.update({"state": "ready", "detail": "API key configured; provider not yet verified", "retry_after_seconds": 0})
    return status
