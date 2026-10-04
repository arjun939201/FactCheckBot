from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse,HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from .routes import chat,factcheck,history,health
app=FastAPI(title="Fact Check",description="Evidence-first fact checking with Grok",version="1.0.0")
app.add_middleware(CORSMiddleware,allow_origins=["*"],allow_methods=["GET","POST","DELETE"],allow_headers=["*"])
app.include_router(health.router,prefix="/api");app.include_router(chat.router,prefix="/api");app.include_router(factcheck.router,prefix="/api");app.include_router(history.router,prefix="/api")
frontend=Path(__file__).resolve().parents[1]/"frontend"
@app.get("/",include_in_schema=False)
def index():return FileResponse(frontend/"index.html")
@app.get("/styles.css",include_in_schema=False)
def styles():return FileResponse(frontend/"styles.css",media_type="text/css")
@app.get("/app.js",include_in_schema=False)
def script():return FileResponse(frontend/"app.js",media_type="application/javascript")
@app.get("/share/{id}",include_in_schema=False)
def share(id:int):
    return HTMLResponse(f"""<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Fact Check Result</title><style>body{{font:16px system-ui;max-width:760px;margin:40px auto;padding:20px;background:#f6f7f9}}pre{{white-space:pre-wrap;background:white;padding:20px;border-radius:14px;border:1px solid #ddd}}</style></head><body><h1>Fact Check</h1><p>Check claims. Follow the evidence.</p><pre id="r">Loading…</pre><script>fetch("/api/history/{id}").then(r=>r.json()).then(x=>document.getElementById("r").textContent=JSON.stringify(x,null,2)).catch(()=>document.getElementById("r").textContent="Result unavailable.")</script></body></html>""")
