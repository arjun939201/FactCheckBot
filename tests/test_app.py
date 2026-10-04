import pytest
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

    async def fake_search(claim):
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

    result=await fc.run_fact_check("Test claim", {"content_mode":"auto"})
    assert result.verdict.value == "TRUE"
    assert str(result.sources[0].url) == "https://example.com/evidence"
    assert result.claims_checked[0].supporting_evidence_ids == ["E01"]


@pytest.mark.asyncio
async def test_vision_429_retries_then_raises_rate_limit(monkeypatch):
    import app.services.media as media

    class FakeResponse:
        status_code=429
        headers={"Retry-After":"0"}
        request=object()
        def raise_for_status(self):
            raise AssertionError("429 response should be handled before raise_for_status")

    class FakeClient:
        calls=0
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def post(self,*args,**kwargs):
            self.calls+=1
            return FakeResponse()

    fake=FakeClient()
    monkeypatch.setattr(media.httpx,"AsyncClient",lambda *a,**k: fake)
    monkeypatch.setattr(media,"get_settings",lambda: type("S",(),{"groq_api_key":"test-key","request_timeout":1.0,"media_vision_retry_attempts":2,"media_vision_retry_max_delay":6.0,"media_vision_calls_per_request":6})())
    async def no_sleep(delay): pass
    monkeypatch.setattr(media.asyncio,"sleep",no_sleep)
    with pytest.raises(media.MediaRateLimitError):
        await media._call_vision_model("vision-model",b"x","image/jpeg","{}")
    assert fake.calls==3


def test_media_rate_limit_is_not_generic_502(monkeypatch):
    import app.routes.factcheck as route
    async def fake_extract(file,budget):
        raise route.MediaRateLimitError("provider throttled",retry_after=9)
    monkeypatch.setattr(route,"extract_media",fake_extract)
    r=client.post("/api/fact-check/media",files={"files":("x.jpg",b"x","image/jpeg")})
    assert r.status_code==429
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
    async def fake_search(claim):
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
    async def fake_search(claim):
        return [{"url":"https://example.com/legal","title":"Legal source","publisher":"Example","content":"Legal evidence","source_type":"web","source_quality":90,"source_tier":"official"}]
    async def fake_groq(instruction):
        return {"claim":"The image says the Prime Minister is illegal","verdict":"UNVERIFIED","confidence":10,"summary":"Insufficient.","reasoning":"Insufficient.","supporting_evidence":[],"contradicting_evidence":[],"sources":[],"claims_checked":[{"claim":"The image says the Prime Minister is illegal","content_type":"FACTUAL CLAIM","verdict":"UNVERIFIED","confidence":10,"summary":"Insufficient.","supporting_evidence_ids":[],"contradicting_evidence_ids":[],"source_quality":0,"corroboration_count":0,"reasoning":"Insufficient.","what_would_change_conclusion":"Relevant evidence."}]}
    monkeypatch.setattr(fc,"decompose_claims",fake_decompose);monkeypatch.setattr(fc,"search_web",fake_search);monkeypatch.setattr(fc,"groq_json",fake_groq)
    from app.services.media import MediaContext
    result=await fc.run_fact_check("", {"content_mode":"auto"}, [MediaContext("image.jpg","image/jpeg",10,"image","MODI IS AN ILLEGAL PM","political image")], media_only=True)
    assert len(result.claims_checked)==1
