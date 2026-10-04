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
    assert r.status_code in (200,502)


@pytest.mark.asyncio
async def test_run_fact_check_normalizes_string_evidence(monkeypatch):
    import app.services.fact_checker as fc

    async def fake_decompose(text, prefs):
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
    assert result.sources[0].url == "https://example.com/evidence"
    assert result.claims_checked[0].supporting_evidence_ids == ["E01"]
