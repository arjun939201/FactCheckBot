import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.models.factcheck import FactCheckRequest,FactCheckResult,Verdict
from app.services.source_validator import normalize_url,allowed_source_url

client=TestClient(app)

def test_health():
    r=client.get("/api/health")
    assert r.status_code==200
    assert r.json()["status"]=="ok"

def test_empty_claim():
    with pytest.raises(Exception):
        FactCheckRequest(text="")

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

def test_root():
    r=client.get("/")
    assert r.status_code==200
    assert "Fact Check" in r.text

def test_chat_failure_is_safe():
    r=client.post("/api/chat",json={"message":"hello"})
    assert r.status_code in (200,502)
