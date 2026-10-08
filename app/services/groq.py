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
        # Retired/stale model names can remain in Render environment variables.
        # A 404 must be treated as configuration fallback, not as a request failure.
        try:
            content=await _request(model,instruction)
            return _parse_json(content)
        except httpx.HTTPStatusError as e:
            last_error=e
            if e.response.status_code!=404:
                raise
            logger.warning("Configured Groq model unavailable; skipping: %s",model)
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
Return ONLY JSON: {{"claims":[{{"claim":"string","content_type":"FACTUAL CLAIM|ARGUMENT|OPINION|PROPAGANDA|PREDICTION|QUESTION|SATIRE/UNCLEAR|MIXED"}}]}}
Rules: preserve the user's meaning; do not fact-check or assign truth; do not invent claims; never turn uncertainty into a separate question; split only genuinely independent assertions. Maximum 4 components."""
    data = await groq_json(prompt)
    if not isinstance(data, dict):
        return []
    items = data.get("claims", [])
    return [x for x in items if isinstance(x, dict) and str(x.get("claim", "")).strip()][:8]


def factcheck_instruction(claim, evidence, prefs, claim_units, media_context="", media_only=False):
    media_section = (f"Attached media is contextual material only. It may help interpret or corroborate the primary input, but it MUST NOT create additional claims or questions.\\nMEDIA CONTEXT:\\n{media_context}" if media_context else "No media context supplied.")
    is_question = any(str(x.get("content_type","")).upper() == "QUESTION" for x in claim_units if isinstance(x, dict))
    if media_only:
        subject_rule = "Assess only substantive claims actually presented by the media. Do not create image-description or identity questions."
    elif is_question:
        subject_rule = "The primary input is a QUESTION. Answer that exact question directly in summary. Do NOT convert it into a factual claim and do NOT give a truth-status verdict. Use only the retrieved evidence. If the evidence establishes the answer, state it plainly (for example, 'No.' or 'Yes.') and briefly explain why. Map evidence IDs that support the answer into sources and/or supporting_evidence. If the evidence genuinely cannot establish the answer, say that clearly instead. claims_checked may be empty for a question."
    else:
        subject_rule = "Return assessments ONLY for the supplied primary text claim units. Do not assess the media as separate claims. Do not add questions about the person, image, date, location, authenticity, or legality unless those are explicitly asserted in the primary text."
    task = "Answer the user's question" if is_question and not media_only else "Fact-check one investigation"
    return f"""{task}. PRIMARY INPUT: {claim!r}
Preferences: {json.dumps(prefs)}
Primary claim units:
{json.dumps(claim_units, ensure_ascii=False)}
{media_section}
Retrieved evidence (ONLY permitted external evidence). Prefer evidence with higher relevance_score; ignore items whose relevance_score is low or whose relevance_reason shows only incidental keyword overlap:
{json.dumps(evidence, ensure_ascii=False)}
{subject_rule}
Return ONE coherent report. Use exact evidence IDs only. Never invent IDs, sources, quotations, dates, or facts. For a question, summary MUST be the direct answer to the user's question; verdict is only an internal placeholder and must not be presented as a truth assessment. Do not phrase the answer as 'no evidence' when the supplied evidence actually establishes the answer. For claims, if evidence is insufficient, use UNVERIFIED.
Return exactly one JSON object using this schema:
{json.dumps(SCHEMA)}"""


    
async def plan_resources(text: str, questions: list[str], prefs: dict) -> dict:
    prompt = f"""Create a context-aware evidence resource plan for this investigation.
USER INPUT: {text[:get_settings().groq_claim_input_chars]!r}
RESEARCH QUESTIONS: {json.dumps(questions, ensure_ascii=False)}
PREFERENCES: {json.dumps(prefs)}
Choose resource types that can actually answer these questions: government/official, courts/law, election authority, legislation/regulations, academic/research, datasets/statistics, company/technical docs, standards/specifications, security advisories, medical/health authorities, financial/regulatory, reputable news, fact-checking, local/primary records, user-provided documents, general web.
Preferred domains must be real and relevant; leave empty when uncertain. Search strategy contains short query tactics, not URLs.
Rules: do not assume the topic is political; adapt to the input. Prefer primary sources, then high-quality secondary sources. Do not invent a source merely to fill a category.
Return ONLY JSON:
{{"context":"string","resource_types":["string"],"preferred_domains":["example.org"],"search_strategy":["string"],"rationale":"string"}}"""
    data = await groq_json(prompt)
    return data if isinstance(data, dict) else {}

async def breakdown_questions(text: str, prefs: dict) -> list[str]:
    prompt = f"""Break the user's input into the smallest set of answerable research questions needed to investigate it.
USER INPUT: {text[:get_settings().groq_claim_input_chars]!r}
PREFERENCES: {json.dumps(prefs)}
Rules:
- The user's input is compulsory and is the only investigation subject.
- Questions must directly help answer the input.
- Prefer concrete, independently researchable questions.
- Do not invent allegations, people, dates, locations, motives, or subclaims not present or logically necessary.
- For a simple factual claim, return 1-3 questions. For a complex claim, return up to 5.
Return ONLY JSON: {{"questions":["string"]}}"""
    data = await groq_json(prompt)
    if not isinstance(data, dict): return []
    return [str(x).strip() for x in data.get("questions",[]) if str(x).strip()][:5]


def research_instruction(input_text: str, questions: list[str], research_packets: list[dict]) -> str:
    return f"""You are the research extraction stage of an evidence-first fact checker.
USER INPUT:
{input_text[:9000]}
RESEARCH QUESTIONS:
{json.dumps(questions, ensure_ascii=False)}
RAW WEB RESEARCH:
{json.dumps(research_packets, ensure_ascii=False)}
For EACH research question, answer ONLY from the supplied web research. Do not use model memory. If the supplied research does not answer a question, say "Insufficient retrieved evidence." Keep answers factual and traceable to evidence IDs. Do not give a final verdict yet.
Return ONLY JSON:
{{"research_data":[{{"question":"string","answer":"string","evidence_ids":["E01"]}}]}}"""
