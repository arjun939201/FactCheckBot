import pytest
import re
from pathlib import Path
from fastapi.testclient import TestClient
from app.main import app
from app.models.factcheck import FactCheckRequest,FactCheckResult,Verdict,ContentType,ClaimAssessment
from app.services.source_validator import normalize_url,allowed_source_url

client=TestClient(app)

def test_health():
    r=client.get("/api/health")
    assert r.status_code==200
    assert r.json()["status"]=="ok"

def test_empty_claim():
    with pytest.raises(Exception): FactCheckRequest(text="")

def test_content_modes():
    assert FactCheckRequest(text="x",content_mode="propaganda").content_mode=="propaganda"
    assert ContentType.PROPAGANDA.value=="PROPAGANDA"

def test_verdicts():
    assert Verdict.FALSE.value=="FALSE"
    assert Verdict.UNVERIFIED.value=="UNVERIFIED"

def test_confidence():
    with pytest.raises(Exception):
        FactCheckResult(claim="x",verdict="FALSE",confidence=101,summary="x",reasoning="x")

def test_urls():
    assert normalize_url("https://example.com/x")
    assert normalize_url("ftp://example.com") is None

def test_allowlist():
    assert allowed_source_url("https://example.com",{"https://example.com"})
    assert not allowed_source_url("https://evil.example",{"https://example.com"})

def test_claim_assessment_schema():
    c=ClaimAssessment(claim="A happened",verdict="TRUE",confidence=80,summary="supported",supporting_evidence_ids=["E01"],source_quality=90,corroboration_count=2)
    assert c.supporting_evidence_ids==["E01"];assert c.source_quality==90

def test_root_has_media_input():
    r=client.get("/")
    assert r.status_code==200
    assert "Fact Check" in r.text
    assert 'id="mediaInput"' in r.text

def test_media_requires_text_or_file():
    r=client.post("/api/fact-check/media",data={"text":""})
    assert r.status_code==400

def test_chat_failure_is_safe():
    r=client.post("/api/chat",json={"message":"hello"})
    assert r.status_code in (200,502,503)


@pytest.mark.asyncio
async def test_run_fact_check_normalizes_string_evidence(monkeypatch):
    import app.services.fact_checker as fc

    async def fake_decompose(text, prefs, media_only=False):
        return [{"claim": text, "content_type": "FACTUAL CLAIM"}]

    async def fake_search(claim, **kwargs):
        return [{
            "url": "https://example.com/evidence",
            "title": "Evidence",
            "publisher": "Example",
            "content": "Evidence excerpt",
            "source_type": "web",
            "source_quality": 60,
            "source_tier": 4,
        }]

    async def fake_groq(instruction):
        return {
            "claim": "Test claim",
            "verdict": "TRUE",
            "confidence": 80,
            "summary": "Supported",
            "reasoning": "Grounded in supplied evidence.",
            "supporting_evidence": ["https://example.com/evidence"],
            "contradicting_evidence": [],
            "sources": ["https://example.com/evidence"],
            "claims_checked": [{
                "claim": "Test claim",
                "content_type": "FACTUAL CLAIM",
                "verdict": "TRUE",
                "confidence": 80,
                "summary": "Supported",
                "supporting_evidence_ids": ["E01"],
                "contradicting_evidence_ids": [],
                "source_quality": 60,
                "corroboration_count": 1,
                "reasoning": "Supported.",
                "what_would_change_conclusion": "New contradictory evidence."
            }]
        }

    monkeypatch.setattr(fc, "decompose_claims", fake_decompose)
    monkeypatch.setattr(fc, "search_web", fake_search)
    monkeypatch.setattr(fc, "groq_json", fake_groq)
    async def fake_questions(text, prefs): return [text]
    monkeypatch.setattr(fc, "breakdown_questions", fake_questions, raising=False)

    result=await fc.run_fact_check("Test claim", {"content_mode":"auto"})
    assert result.verdict.value == "TRUE"
    assert str(result.sources[0].url) == "https://example.com/evidence"
    assert result.claims_checked[0].supporting_evidence_ids == ["E01"]


@pytest.mark.asyncio
async def test_vision_429_waits_and_retries_same_stage(monkeypatch):
    import app.services.media as media
    import app.services.groq as groq

    for name,value in {
        "_ai_active_requests":0,"_ai_state":"unknown","_ai_detail":"test",
        "_ai_reset_at":0.0,"_ai_last_success_at":0.0,
        "_ai_rate_limit_kind":"unknown","_ai_required_tokens_estimate":None,
    }.items():
        monkeypatch.setattr(groq,name,value)

    class FakeResponse:
        def __init__(self, status_code):
            self.status_code=status_code
            self.headers={"Retry-After":"0"}
            self.request=object()
            self.text=""
        def json(self):
            if self.status_code==429:
                return {"error":{"message":"TPM rate limit: requested 100 tokens"}}
            return {"choices":[{"message":{"content":'{"visible_text":"ok","visual_summary":"clear","claims_or_context":[],"uncertainties":[]}'}}]}
        def raise_for_status(self):
            if self.status_code>=400:
                raise AssertionError("successful response expected")

    class FakeClient:
        calls=0
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def post(self,*args,**kwargs):
            self.calls+=1
            return FakeResponse(429 if self.calls<3 else 200)

    fake=FakeClient()
    monkeypatch.setattr(media.httpx,"AsyncClient",lambda *a,**k: fake)
    monkeypatch.setattr(media,"get_settings",lambda: type("S",(),{"groq_api_key":"test-key","request_timeout":1.0,"media_vision_retry_attempts":2,"media_vision_retry_max_delay":6.0,"media_vision_calls_per_request":6})())
    async def no_sleep(delay): pass
    monkeypatch.setattr(media.asyncio,"sleep",no_sleep)
    result=await media._call_vision_model("vision-model",b"x","image/jpeg","{}")
    assert result["visible_text"]=="ok"
    assert fake.calls==3


def test_media_rate_limit_is_not_generic_502(monkeypatch):
    import app.routes.factcheck as route
    async def fake_extract(file,budget):
        raise route.MediaRateLimitError("provider throttled",retry_after=9)
    monkeypatch.setattr(route,"extract_media",fake_extract)
    r=client.post("/api/fact-check/media",files={"files":("x.jpg",b"x","image/jpeg")})
    # Media extraction is optional context when no text was supplied; if it is
    # rate-limited, the route may continue and return a clear provider error.
    assert r.status_code in (429, 503)
    if r.status_code == 429:
        assert r.headers.get("retry-after")=="9"


def test_text_model_discovery_excludes_guard_models():
    from app.services.groq import _text_model_candidates
    models={"meta-llama/llama-prompt-guard-2-22m","openai/gpt-oss-120b","llama-3.3-70b-versatile","whisper-large-v3-turbo"}
    candidates=_text_model_candidates(models)
    assert "meta-llama/llama-prompt-guard-2-22m" not in candidates
    assert "whisper-large-v3-turbo" not in candidates
    assert candidates[0] == "openai/gpt-oss-120b"


def test_groq_payload_budget():
    from app.services.groq import _clip_instruction,GroqPayloadTooLargeError
    with pytest.raises(GroqPayloadTooLargeError):
        _clip_instruction("x" * 28001)


def test_media_route_maps_payload_too_large():
    import app.routes.factcheck as route
    async def fake_extract(file,budget):
        from app.services.media import MediaContext
        return MediaContext("x.jpg","image/jpeg",1,"image")
    async def fake_run(*args,**kwargs):
        raise route.GroqPayloadTooLargeError("too large")
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(route,"extract_media",fake_extract)
    monkeypatch.setattr(route,"run_fact_check",fake_run)
    try:
        r=client.post("/api/fact-check/media",files={"files":("x.jpg",b"x","image/jpeg")})
        assert r.status_code==413
    finally:
        monkeypatch.undo()


@pytest.mark.asyncio
async def test_media_does_not_create_separate_claims_when_text_is_primary(monkeypatch):
    import app.services.fact_checker as fc
    calls=[]
    async def fake_decompose(text, prefs, media_only=False):
        calls.append((text,media_only))
        return [{"claim":"CJP and congress are driving youth against the ruling party and Modi", "content_type":"FACTUAL CLAIM"}]
    async def fake_search(claim, **kwargs):
        return [{"url":"https://example.com/relevant","title":"Relevant","publisher":"Example","content":"Relevant evidence","source_type":"web","source_quality":80,"source_tier":"news"}]
    async def fake_groq(instruction):
        return {"claim":"CJP and congress are driving youth against the ruling party and Modi","verdict":"UNVERIFIED","confidence":20,"summary":"Insufficient relevant evidence.","reasoning":"The retrieved material does not establish the claim.","supporting_evidence":[],"contradicting_evidence":[],"sources":[],"claims_checked":[
            {"claim":"CJP and congress are driving youth against the ruling party and Modi","content_type":"FACTUAL CLAIM","verdict":"UNVERIFIED","confidence":20,"summary":"Insufficient.","supporting_evidence_ids":[],"contradicting_evidence_ids":[],"source_quality":0,"corroboration_count":0,"reasoning":"Insufficient.","what_would_change_conclusion":"Relevant evidence."},
            {"claim":"MODI IS AN ILLEGAL PM","content_type":"FACTUAL CLAIM","verdict":"UNVERIFIED","confidence":0,"summary":"Media-only claim","supporting_evidence_ids":[],"contradicting_evidence_ids":[],"source_quality":0,"corroboration_count":0,"reasoning":"Should not be included.","what_would_change_conclusion":""},
            {"claim":"Whether the pictured person is Kharge","content_type":"QUESTION","verdict":"UNVERIFIED","confidence":0,"summary":"Question","supporting_evidence_ids":[],"contradicting_evidence_ids":[],"source_quality":0,"corroboration_count":0,"reasoning":"Should not be included.","what_would_change_conclusion":""}
        ]}
    monkeypatch.setattr(fc,"decompose_claims",fake_decompose)
    monkeypatch.setattr(fc,"search_web",fake_search)
    monkeypatch.setattr(fc,"groq_json",fake_groq)
    async def fake_questions(text, prefs): return [text]
    monkeypatch.setattr(fc,"breakdown_questions",fake_questions,raising=False)
    from app.services.media import MediaContext
    result=await fc.run_fact_check("CJP and congress are driving youth against the ruling party and Modi", {"content_mode":"auto"}, [MediaContext("image.jpg","image/jpeg",10,"image","MODI IS AN ILLEGAL PM","political image")])
    assert calls[0][1] is False
    assert len(result.claims_checked)==1
    assert "MODI IS AN ILLEGAL PM" not in result.claims_checked[0].claim


@pytest.mark.asyncio
async def test_media_only_can_generate_substantive_claims(monkeypatch):
    import app.services.fact_checker as fc
    async def fake_decompose(text, prefs, media_only=False):
        assert media_only is True
        return [{"claim":"The image says the Prime Minister is illegal", "content_type":"FACTUAL CLAIM"}]
    async def fake_search(claim, **kwargs):
        return [{"url":"https://example.com/legal","title":"Legal source","publisher":"Example","content":"Legal evidence","source_type":"web","source_quality":90,"source_tier":"official"}]
    async def fake_groq(instruction):
        return {"claim":"The image says the Prime Minister is illegal","verdict":"UNVERIFIED","confidence":10,"summary":"Insufficient.","reasoning":"Insufficient.","supporting_evidence":[],"contradicting_evidence":[],"sources":[],"claims_checked":[{"claim":"The image says the Prime Minister is illegal","content_type":"FACTUAL CLAIM","verdict":"UNVERIFIED","confidence":10,"summary":"Insufficient.","supporting_evidence_ids":[],"contradicting_evidence_ids":[],"source_quality":0,"corroboration_count":0,"reasoning":"Insufficient.","what_would_change_conclusion":"Relevant evidence."}]}
    monkeypatch.setattr(fc,"decompose_claims",fake_decompose);monkeypatch.setattr(fc,"search_web",fake_search);monkeypatch.setattr(fc,"groq_json",fake_groq)
    async def fake_questions(text, prefs): return [text]
    monkeypatch.setattr(fc,"breakdown_questions",fake_questions,raising=False)
    from app.services.media import MediaContext
    result=await fc.run_fact_check("", {"content_mode":"auto"}, [MediaContext("image.jpg","image/jpeg",10,"image","MODI IS AN ILLEGAL PM","political image")], media_only=True)
    assert len(result.claims_checked)==1


def test_search_relevance_rejects_unrelated_acronym_hits():
    from app.services.search import _relevance
    unrelated={"title":"California Commission on Judicial Performance","publisher":"ccjp.ca.gov","content":"Judicial discipline and court performance."}
    relevant={"title":"Congress and Modi youth politics in India","publisher":"example.in","content":"Congress and Modi are discussed in Indian political reporting."}
    assert _relevance("CJP and Congress are driving youth against Modi", unrelated) < 50
    assert unrelated["relevant"] is False
    assert _relevance("CJP and Congress are driving youth against Modi", relevant) > 0
    assert relevant["relevant"] is True


@pytest.mark.asyncio
async def test_fact_checker_never_maps_weak_search_results(monkeypatch):
    import app.services.fact_checker as fc
    async def fake_decompose(text,prefs,media_only=False):
        return [{"claim":text,"content_type":"FACTUAL CLAIM"}]
    async def fake_search(claim, **kwargs):
        return [{"url":"https://bad.example","title":"Unrelated","publisher":"bad.example","content":"Nothing relevant","source_type":"Other","source_quality":60,"source_tier":"Other","relevant":False,"relevance_score":0,"relevance_reason":"0/5 key terms match"}]
    async def fake_groq(instruction):
        return {"claim":"Test claim","verdict":"TRUE","confidence":90,"summary":"Looks supported","reasoning":"Model tried to map weak evidence.","supporting_evidence":["https://bad.example"],"contradicting_evidence":[],"sources":["https://bad.example"],"claims_checked":[{"claim":"Test claim","content_type":"FACTUAL CLAIM","verdict":"TRUE","confidence":90,"summary":"Looks supported","supporting_evidence_ids":["E01"],"contradicting_evidence_ids":[],"source_quality":60,"corroboration_count":1,"reasoning":"Weak","what_would_change_conclusion":"Relevant evidence."}]}
    monkeypatch.setattr(fc,"decompose_claims",fake_decompose);monkeypatch.setattr(fc,"search_web",fake_search);monkeypatch.setattr(fc,"groq_json",fake_groq)
    async def fake_questions(text, prefs): return [text]
    monkeypatch.setattr(fc,"breakdown_questions",fake_questions,raising=False)
    result=await fc.run_fact_check("Test claim",{"content_mode":"auto"})
    assert result.sources==[]
    assert result.supporting_evidence==[]
    assert result.verdict.value=="UNVERIFIED"


def test_frontend_microcopy_and_upload_ux():
    from pathlib import Path
    html=Path("frontend/index.html").read_text()
    js=Path("frontend/app.js").read_text()
    assert "Check the claim." in html
    assert "See the evidence." in html
    assert "claim-input-wrap" in html
    assert "Investigate" in html
    assert "mobile-tabs" in html
    assert "class=\"claim-attach\"" in html
    assert "removeFile" in js
    assert "dataTransfer.files" in js
    # Navigation queries must use querySelectorAll helper ($$), not querySelector ($).
    assert "$$('.nav-btn,.mobile-tab').forEach" in js
    assert "$$('.view').forEach" in js


def test_share_tokens_are_random_and_owner_authorized(monkeypatch, tmp_path):
    from app.services import history as history_service

    settings=type("Settings",(),{
        "database_url":f"sqlite:///{tmp_path / 'history.db'}",
        "max_history_items":20,
    })()
    monkeypatch.setattr(history_service,"get_settings",lambda:settings)
    store=history_service.HistoryStore()
    item_id=store.add("claim","Private claim","UNVERIFIED",0,{"claim":"Private claim"},"now","owner-a")

    token=store.create_share(item_id,"owner-a")
    assert token and len(token)>=40
    assert store.create_share(item_id,"owner-a")==token
    assert store.create_share(item_id,"owner-b") is None
    assert store.get_shared(token)["claim"]=="Private claim"
    assert store.get_shared(str(item_id)) is None


@pytest.mark.asyncio
async def test_request_guard_rejects_malformed_content_length(monkeypatch):
    import app.middleware as middleware

    settings=type("Settings",(),{"rate_limit_per_minute":20,"max_request_bytes":1024})()
    monkeypatch.setattr(middleware,"get_settings",lambda:settings)
    monkeypatch.setattr(middleware._guard,"allow",lambda *args:True)

    class RequestStub:
        method="POST"
        url=type("URL",(),{"path":"/api/chat"})()
        client=type("Client",(),{"host":"127.0.0.1"})()
        headers={"content-length":"not-a-number"}

    async def next_handler(request):
        raise AssertionError("invalid request must be rejected before reaching the route")

    response=await middleware.request_guard(RequestStub(),next_handler)
    assert response.status_code==400


def test_media_only_investigation_is_supported(monkeypatch):
    import app.routes.factcheck as route
    from app.models.factcheck import FactCheckResult
    from app.services.media import MediaContext

    async def fake_extract(file,budget):
        return MediaContext("x.jpg","image/jpeg",1,"image",extracted_text="A visible claim")
    async def fake_run(text,prefs,contexts,media_only=False):
        assert media_only is True
        assert contexts and contexts[0].extracted_text=="A visible claim"
        return FactCheckResult(
            claim=text,verdict="UNVERIFIED",confidence=0,
            summary="Insufficient evidence.",reasoning="No live evidence supplied."
        )
    async def fake_save(*args,**kwargs):
        return 987

    monkeypatch.setattr(route,"extract_media",fake_extract)
    monkeypatch.setattr(route,"run_fact_check",fake_run)
    monkeypatch.setattr(route,"_save",fake_save)
    response=client.post("/api/fact-check/media",files={"files":("x.jpg",b"x","image/jpeg")})
    assert response.status_code==200
    assert response.json()["id"]==987


def test_successful_ai_retry_clears_stale_rate_limit(monkeypatch):
    import time
    import app.services.groq as groq

    monkeypatch.setattr(groq,"_ai_active_requests",1)
    monkeypatch.setattr(groq,"_ai_state","unavailable")
    monkeypatch.setattr(groq,"_ai_detail","AI rate limit reached")
    monkeypatch.setattr(groq,"_ai_last_success_at",0.0)
    monkeypatch.setattr(groq,"_ai_reset_at",time.monotonic()+60)
    monkeypatch.setattr(groq,"_ai_rate_limit_kind","tpm")
    monkeypatch.setattr(groq,"_ai_required_tokens_estimate",100)
    groq.ai_request_succeeded()
    assert groq._ai_reset_at==0
    assert groq._ai_rate_limit_kind=="unknown"
    assert groq._ai_required_tokens_estimate is None


def test_article_fetcher_rejects_private_and_non_http_targets():
    from app.services.article_parser import _public_http_url

    assert not _public_http_url("http://127.0.0.1/")
    assert not _public_http_url("http://169.254.169.254/latest/meta-data/")
    assert not _public_http_url("http://localhost/")
    assert not _public_http_url("ftp://example.com/")
    assert not _public_http_url("http://example.com:8080/")


@pytest.mark.asyncio
async def test_article_fetcher_validates_redirect_before_following(monkeypatch):
    import app.services.article_parser as parser

    class RedirectResponse:
        status_code=302
        headers={"location":"http://127.0.0.1/private"}
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass

    class FakeClient:
        calls=[]
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        def stream(self,method,url):
            self.calls.append(url)
            return RedirectResponse()

    fake=FakeClient()
    monkeypatch.setattr(parser.httpx,"AsyncClient",lambda *a,**k:fake)
    monkeypatch.setattr(parser,"_public_http_url",lambda url:url=="https://public.example/start")
    with pytest.raises(parser.ArticleFetchError):
        await parser._request_limited("https://public.example/start","test",1,1024)
    assert fake.calls==["https://public.example/start"]


def test_progress_wait_resumes_previous_stage():
    from app.services import progress

    context_token=progress._current_id.set("progress-resume-test")
    try:
        progress.begin_progress("progress-resume-test")
        progress.update_progress("researching")
        progress.update_progress("waiting_ai")
        assert progress.get_progress("progress-resume-test")["stage"]=="waiting_ai"
        progress.update_progress("resume")
        assert progress.get_progress("progress-resume-test")["stage"]=="researching"
    finally:
        progress._current_id.reset(context_token)
        with progress._lock:
            progress._progress.pop("progress-resume-test",None)


def test_frontend_html_ids_are_unique():
    html = (Path(__file__).resolve().parents[1] / "frontend" / "index.html").read_text(encoding="utf-8")
    ids = re.findall(r"\bid=[\"']([^\"']+)[\"']", html)
    duplicates = sorted({value for value in ids if ids.count(value) > 1})
    assert not duplicates, f"Duplicate HTML IDs: {duplicates}"


def test_frontend_progress_stages_match_backend():
    script = (Path(__file__).resolve().parents[1] / "frontend" / "app.js").read_text(encoding="utf-8")
    assert "const progressStages=['breaking','researching','collecting','analyzing']" in script


def test_share_page_script_is_served():
    response = client.get("/share.js")
    assert response.status_code == 200
    assert "api/share/" in response.text


def test_production_settings_reject_ephemeral_database_and_wildcards():
    from pydantic import ValidationError
    from app.config import Settings

    with pytest.raises(ValidationError, match="managed PostgreSQL"):
        Settings(
            app_env="production",
            groq_api_key="test-key",
            database_url="sqlite:///./factcheck.db",
            cors_origins="*",
            allowed_hosts="*",
        )


def test_production_settings_accept_explicit_postgres_and_domains():
    from app.config import Settings

    settings = Settings(
        app_env="production",
        groq_api_key="test-key",
        database_url="postgresql://user:password@localhost:5432/factcheck",
        cors_origins="https://factcheck.example",
        allowed_hosts="factcheck.example",
    )
    assert settings.database_url.startswith("postgresql://")
    assert settings.cors_origin_list == ["https://factcheck.example"]
    assert settings.allowed_host_list == ["factcheck.example"]
