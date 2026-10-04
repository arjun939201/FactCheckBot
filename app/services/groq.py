import json
import logging
import re
import time

import httpx

from ..config import get_settings

logger = logging.getLogger(__name__)
SYSTEM_PROMPT = """You are the analysis engine for a neutral evidence-first fact-checking application. Use ONLY the evidence supplied in the user message for evidence-based conclusions. Never invent sources, URLs, quotations, statistics, dates, studies, organizations, or evidence. If evidence is insufficient, use UNVERIFIED. Distinguish facts, uncertainty, opinions, predictions, and questions. Do not treat AI knowledge as independently verified evidence. Confidence is assessment confidence, not a mathematical probability. A statement about a person's or group's alleged motive, conduct, propaganda, discrimination, or political behavior is still a FACTUAL CLAIM when it asserts that something happened or is happening; do not label it OPINION merely because it concerns motives or politics. Reserve OPINION for subjective judgments, preferences, beliefs, or value statements that do not assert a verifiable event or state of affairs. Return ONLY valid JSON."""
SCHEMA = {"claim":"string","verdict":"TRUE|MOSTLY TRUE|PARTLY TRUE|MISLEADING|MOSTLY FALSE|FALSE|UNVERIFIED|OPINION|PREDICTION","confidence":0,"summary":"string","reasoning":"string","key_points":["string"],"supporting_evidence":[],"contradicting_evidence":[],"context":"string","sources":[],"uncertainties":[],"content_type":"FACTUAL CLAIM|ARGUMENT|OPINION|PROPAGANDA|PREDICTION|QUESTION|SATIRE/UNCLEAR|MIXED","report_title":"string","report_sections":["string"],"claims_checked":[]}

_model_cache: tuple[float, set[str]] | None = None
MODEL_CACHE_SECONDS = 300


class GroqProviderError(RuntimeError):
    """A provider-side failure that should not be confused with an app bug."""


class GroqPayloadTooLargeError(GroqProviderError):
    """The request is too large for the selected provider model."""


def _is_text_generation_model(model: str) -> bool:
    """Reject guard/classifier/transcription models from generic chat fallback."""
    m=model.lower()
    blocked=(
        "prompt-guard", "guard", "safeguard", "moderation", "moderator",
        "whisper", "distil-whisper", "tts", "text-to-speech", "embed",
        "embedding", "rerank", "reranker", "speech-to-text",
    )
    if any(x in m for x in blocked):
        return False
    families=("gpt-oss", "llama-3", "llama-4", "qwen", "mixtral", "gemma")
    return any(x in m for x in families)


def _text_model_candidates(models: set[str]) -> list[str]:
    def score(model: str) -> tuple[int, str]:
        m=model.lower()
        if "gpt-oss" in m: rank=0
        elif "llama-3.3" in m: rank=1
        elif "llama-3" in m: rank=2
        elif "qwen" in m: rank=3
        elif "llama-4" in m: rank=4
        elif "mixtral" in m: rank=5
        else: rank=6
        return rank,m
    return sorted((m for m in models if _is_text_generation_model(m)), key=score)


async def _available_models() -> set[str]:
    global _model_cache
    now=time.monotonic()
    if _model_cache and now-_model_cache[0] < MODEL_CACHE_SECONDS:
        return _model_cache[1]
    s=get_settings()
    async with httpx.AsyncClient(timeout=min(s.request_timeout,15)) as c:
        r=await c.get("https://api.groq.com/openai/v1/models",headers={"Authorization":f"Bearer {s.groq_api_key}"})
        r.raise_for_status()
        data=r.json()
    models={str(x.get("id")) for x in data.get("data",[]) if isinstance(x,dict) and x.get("id")}
    _model_cache=(now,models)
    return models


def _clip_instruction(instruction: str) -> str:
    limit=get_settings().groq_max_instruction_chars
    if len(instruction)<=limit:
        return instruction
    raise GroqPayloadTooLargeError(
        f"The research request is too large for the AI analysis model. Reduce the amount of attached text or evidence (limit {limit} characters)."
    )


async def _request(model, instruction):
    s=get_settings()
    instruction=_clip_instruction(instruction)
    async with httpx.AsyncClient(timeout=s.request_timeout) as c:
        r=await c.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization":f"Bearer {s.groq_api_key}","Content-Type":"application/json"},
            json={"model":model,"temperature":0.1,"messages":[{"role":"system","content":SYSTEM_PROMPT},{"role":"user","content":instruction}]},
        )
        if r.is_error:
            logger.error("Groq request failed: status=%s model=%s body=%s",r.status_code,model,r.text[:1000])
            if r.status_code==400 and "reduce the length" in r.text.lower():
                raise GroqPayloadTooLargeError("Groq rejected the request because the prompt is too large.")
            r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


async def groq_json(instruction):
    s=get_settings()
    if not s.groq_api_key:
        raise GroqProviderError("Groq API is not configured")
    candidates=[]
    for model in (s.groq_model,s.groq_fallback_model):
        if model and model not in candidates:
            candidates.append(model)
    last_error=None
    for model in candidates:
        try:
            content=await _request(model,instruction)
            return _parse_json(content)
        except httpx.HTTPStatusError as e:
            last_error=e
            if e.response.status_code!=404:
                raise
            logger.warning("Configured Groq model unavailable: %s",model)
    try:
        available=await _available_models()
    except httpx.HTTPError as e:
        logger.exception("Groq model discovery failed")
        raise GroqProviderError("No configured Groq model is available and model discovery failed.") from e
    discovered=_text_model_candidates(available)
    logger.info("Discovered compatible Groq text models: %s",discovered)
    for model in discovered:
        if model in candidates:
            continue
        try:
            logger.info("Retrying Groq request with discovered compatible model=%s",model)
            content=await _request(model,instruction)
            return _parse_json(content)
        except httpx.HTTPStatusError as e:
            if e.response.status_code==404:
                continue
            raise
    raise GroqProviderError("No currently available Groq text-generation model was found.") from last_error


def _parse_json(content: str):
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.I)
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        logger.error("Groq returned invalid JSON: content_prefix=%s", content[:1000])
        raise RuntimeError("Groq returned invalid JSON") from e


async def decompose_claims(text: str, prefs: dict) -> list[dict]:
    prompt = f"""Decompose this input into the smallest meaningful components that should be analyzed separately.
Input: {text[:get_settings().groq_claim_input_chars]!r}
Preferences: {json.dumps(prefs)}
Return ONLY JSON: {{"claims":[{{"claim":"string","content_type":"FACTUAL CLAIM|ARGUMENT|OPINION|PROPAGANDA|PREDICTION|QUESTION|SATIRE/UNCLEAR|MIXED"}}]}}
Rules: preserve the user's meaning; do not fact-check or assign truth; do not invent claims; combine only inseparable fragments; split multiple factual assertions and separate factual assertions from opinions or conclusions. Maximum 8 components."""
    data = await groq_json(prompt)
    if not isinstance(data, dict):
        return []
    items = data.get("claims", [])
    return [x for x in items if isinstance(x, dict) and str(x.get("claim", "")).strip()][:8]


def factcheck_instruction(claim, evidence, prefs, claim_units):
    return f"""Fact-check this input: {claim!r}
Preferences: {json.dumps(prefs)}
Decomposed claim units (research was performed separately for each):
{json.dumps(claim_units, ensure_ascii=False)}
Retrieved evidence (ONLY permitted external evidence):
{json.dumps(evidence, ensure_ascii=False)}
For each decomposed component, return claims_checked with exact evidence IDs from the supplied evidence. Do not invent IDs. Use supporting_evidence_ids and contradicting_evidence_ids to map evidence to that claim. source_quality must reflect the retrieved source quality (0-100), and corroboration_count is the number of distinct retrieved sources that independently support the assessment. For ARGUMENT, analyze premises, conclusion, reasoning gaps and evidence. For OPINION, analyze the viewpoint without pretending a subjective preference can be objectively proven. For PROPAGANDA, identify concrete persuasive techniques only when actually present; political content is not automatically propaganda. For FACTUAL CLAIM, assess verifiable assertions. For QUESTION, explain what would need to be established. For MIXED, separate components.
Return exactly one JSON object using this schema:
{json.dumps(SCHEMA)}
Sources/evidence URLs and IDs must be copied exactly from supplied evidence. If evidence is insufficient, use UNVERIFIED. Never manufacture dates, excerpts, sources, IDs, or facts."""
