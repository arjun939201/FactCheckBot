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


async def decompose_claims(text: str, prefs: dict, media_only: bool = False) -> list[dict]:
    mode_rule = (
        "The input is text supplied by the user. It is the ONLY subject of this investigation. "
        "Do not create claims from attached media, OCR, image descriptions, identity questions, "
        "dates, locations, authenticity, or visual observations."
        if not media_only else
        "There is no user text. Derive only substantive verifiable assertions actually presented "
        "by the media. Do not manufacture identity, authenticity, date, location, or image-description questions."
    )
    prompt = f"""Decompose this investigation subject into the smallest meaningful substantive claims.
Input: {text[:get_settings().groq_claim_input_chars]!r}
Preferences: {json.dumps(prefs)}
{mode_rule}
Return ONLY JSON: {{"claims":[{{"claim":"string","content_type":"FACTUAL CLAIM|ARGUMENT|OPINION|PROPAGANDA|PREDICTION|SATIRE/UNCLEAR|MIXED"}}]}}
Rules: preserve the user's meaning; do not fact-check or assign truth; do not invent claims; never turn uncertainty into a separate question; split only genuinely independent assertions. Maximum 4 components."""
    data = await groq_json(prompt)
    if not isinstance(data, dict):
        return []
    items = data.get("claims", [])
    return [x for x in items if isinstance(x, dict) and str(x.get("claim", "")).strip()][:8]


def factcheck_instruction(claim, evidence, prefs, claim_units, media_context="", media_only=False):
    media_section = (f"Attached media is contextual material only. It may help interpret or corroborate the primary claim, but it MUST NOT create additional claims or questions.\nMEDIA CONTEXT:\n{media_context}" if media_context else "No media context supplied.")
    subject_rule = (
        "Return assessments ONLY for the supplied primary text claim units. Do not assess the media as separate claims. Do not add questions about the person, image, date, location, authenticity, or legality unless those are explicitly asserted in the primary text."
        if not media_only else
        "Assess only substantive claims actually presented by the media. Do not create image-description or identity questions."
    )
    return f"""Fact-check one investigation. PRIMARY INPUT: {claim!r}
Preferences: {json.dumps(prefs)}
Primary claim units:
{json.dumps(claim_units, ensure_ascii=False)}
{media_section}
Retrieved evidence (ONLY permitted external evidence). Prefer evidence with higher relevance_score; ignore items whose relevance_score is low or whose relevance_reason shows only incidental keyword overlap:
{json.dumps(evidence, ensure_ascii=False)}
{subject_rule}
Return ONE coherent report. claims_checked must correspond only to the primary claim units and should normally contain one assessment when the user supplied one substantive claim. Use exact evidence IDs only. Never invent IDs, sources, quotations, dates, or facts. If evidence is insufficient, use UNVERIFIED. Do not write a report about questions that the user did not ask.
Return exactly one JSON object using this schema:
{json.dumps(SCHEMA)}"""
