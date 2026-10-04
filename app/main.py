import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import get_settings
from .routes import chat, factcheck, health, history

settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    description="Evidence-first fact checking with live web research and AI-assisted analysis.",
    version=settings.app_version,
    docs_url="/docs" if settings.app_env != "production" else None,
    redoc_url="/redoc" if settings.app_env != "production" else None,
)

if settings.allowed_host_list != ["*"]:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_host_list)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.middleware("http")
async def production_headers(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["X-Response-Time-Ms"] = str(round((time.perf_counter() - started) * 1000, 2))
    return response


app.include_router(health.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(factcheck.router, prefix="/api")
app.include_router(history.router, prefix="/api")

frontend = Path(__file__).resolve().parents[1] / "frontend"


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(frontend / "index.html")


@app.get("/styles.css", include_in_schema=False)
def styles():
    return FileResponse(frontend / "styles.css", media_type="text/css")


@app.get("/app.js", include_in_schema=False)
def script():
    return FileResponse(frontend / "app.js", media_type="application/javascript")


@app.get("/share/{id}", include_in_schema=False)
def share(id: int):
    return HTMLResponse(
        """<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fact Check Result</title><style>body{font:16px system-ui;max-width:760px;margin:40px auto;padding:20px;background:#f6f7f9}
pre{white-space:pre-wrap;background:white;padding:20px;border-radius:14px;border:1px solid #ddd}</style></head>
<body><h1>Fact Check</h1><p>Check claims. Follow the evidence.</p><pre id="r">Loading…</pre>
<script>fetch("/api/share/%d").then(r=>{if(!r.ok)throw Error();return r.json()}).then(x=>document.getElementById("r").textContent=JSON.stringify(x,null,2)).catch(()=>document.getElementById("r").textContent="Result unavailable.")</script></body></html>""" % id
    )
