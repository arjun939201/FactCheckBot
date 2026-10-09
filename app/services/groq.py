import asyncio
import json
import logging
import re
import time
import math

import httpx

from ..config import get_settings

logger = logging.getLogger(__name__)
SYSTEM_PROMPT = """You are the analysis engine for a neutral evidence-first fact-checking application. Use ONLY the evidence supplied in the user message for evidence-based conclusions. Never invent sources, URLs, quotations, statistics, dates, studies, organizations, or evidence. If evidence is insufficient, use UNVERIFIED. Distinguish facts, uncertainty, opinions, predictions, and questions. Do not treat AI knowledge as independently verified evidence. Confidence is assessment confidence, not a mathematical probability. A statement about a person's or group's alleged motive, conduct, propaganda, discrimination, or political behavior is still a FACTUAL CLAIM when it asserts that something happened or is happening; do not label it OPINION merely because it concerns motives or politics. Reserve OPINION for subjective judgments, preferences, beliefs, or value statements that do not assert a verifiable event or state of affairs. Return ONLY valid JSON."""
SCHEMA = {"claim":"string","verdict":"TRUE|MOSTLY TRUE|PARTLY TRUE|MISLEADING|MOSTLY FALSE|FALSE|UNVERIFIED|OPINION|PREDICTION","confidence":0,"summary":"string","reasoning":"string","key_points":["string"],"supporting_evidence":[],"contradicting_evidence":[],"context":"string","sources":[],"uncertainties":[],"content_type":"FACTUAL CLAIM|ARGUMENT|OPINION|PROPAGANDA|PREDICTION|QUESTION|SATIRE/UNCLEAR|MIXED","report_title":"string","report_sections":["string"],"claims_checked":[]}

_model_cache: tuple[float, set[str]] | None = None
MODEL_CACHE_SECONDS = 300

# Process-local live status; cooldown is based on Groq's retry hint.
_ai_state = "unknown"
_ai_detail = "Waiting for the first AI request"
_ai_active_requests = 0
_ai_reset_at = 0.0
_ai_last_success_at = 0.0


def get_ai_status() -> dict:
    now = time.monotonic()
    retry_after = max(0, math.ceil(_ai_reset_at - now))
    last_success_ago = max(0, int(now - _ai_last_success_at)) if _ai_last_success_at else None

    # Report observed request capacity, not a generic claim that the provider is
    # currently accessible. After a provider cooldown expires, only one retry is
    # justified; a successful request is required to verify actual capacity.
    if retry_after:
        state, detail = "rate_limited", "Provider-reported rate-limit wait is still active"
    elif _ai_active_requests:
        state, detail = "busy", "AI request in progress; capacity is being tested"
    elif _ai_state == "unavailable" and _ai_detail == "AI rate limit reached":
        state, detail = "retry_ready", "Cooldown elapsed; one request may be retried, but capacity is not yet verified"
    elif _ai_state == "available" and last_success_ago is not None and last_success_ago <= 90:
        state, detail = "verified", "At least one AI request succeeded recently; remaining capacity is not guaranteed"
    elif _ai_state == "available" and last_success_ago is not None:
        state, detail = "stale", "A request succeeded earlier, but current capacity has not been verified"
    else:
        state, detail = _ai_state, _ai_detail

    return {
        "state": state,
        "detail": detail,
        "retry_after_seconds": retry_after,
        "active_requests": _ai_active_requests,
        "last_success_ago_seconds": last_success_ago,
        "capacity": "blocked" if retry_after else "one_retry_possible" if state == "retry_ready" else "recent_success" if state == "verified" else "unknown",
    }

class GroqProviderError(RuntimeError):
    """A provider-side failure that should not be confused with an app bug."""


class GroqPayloadTooLargeError(GroqProviderError):
    """The request is too large for the selected provider model."""


class GroqRateLimitError(GroqProviderError):
    """Groq temporarily rejected the request because of a rate limit."""

    def __init__(self, message: str, retry_after: float = 10.0):
        super().__init__(message)
        self.retry_after = max(1.0, float(retry_after))


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


async def _request_impl(model, instruction):
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
            if r.status_code == 429:
                retry_after = 10.0
                header = r.headers.get("Retry-After")
                if header:
                    try:
                        retry_after = float(header)
                    except ValueError:
                        pass
                try:
                    message = str(r.json().get("error", {}).get("message", ""))
                except Exception:
                    message = r.text
                match = re.search(r"try again in\s+([0-9.]+)s", message, flags=re.I)
                if match:
                    retry_after = float(match.group(1))
                raise GroqRateLimitError(
                    f"AI analysis is temporarily rate-limited. Please retry in about {max(1, round(retry_after))} seconds.",
                    retry_after=retry_after,
                )
            if r.status_code==400 and "reduce the length" in r.text.lower():
                raise GroqPayloadTooLargeError("Groq rejected the request because the prompt is too large.")
            r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


def ai_request_started():
    global _ai_state, _ai_detail, _ai_active_requests
    _ai_active_requests += 1
    if _ai_reset_at <= time.monotonic():
        _ai_state, _ai_detail = "busy", "AI request in progress"


def ai_request_succeeded():
    global _ai_state, _ai_detail, _ai_last_success_at
    _ai_last_success_at = time.monotonic()
    if _ai_reset_at <= time.monotonic():
        _ai_state, _ai_detail = "available", "Last AI request succeeded"


def record_ai_rate_limit(retry_after: float):
    global _ai_state, _ai_detail, _ai_reset_at
    _ai_state, _ai_detail = "unavailable", "AI rate limit reached"
    _ai_reset_at = time.monotonic() + max(1.0, float(retry_after))


def ai_request_failed():
    global _ai_state, _ai_detail
    if _ai_reset_at <= time.monotonic():
        _ai_state, _ai_detail = "unavailable", "AI provider request failed"


def ai_request_finished():
    global _ai_active_requests
    _ai_active_requests = max(0, _ai_active_requests - 1)


async def _request(model, instruction):
    ai_request_started()
    try:
        result = await _request_impl(model, instruction)
        ai_request_succeeded()
        return result
    except GroqRateLimitError as e:
        record_ai_rate_limit(e.retry_after)
        raise
    except Exception as e:
        if isinstance(e, (httpx.HTTPError, GroqProviderError)):
            ai_request_failed()
        raise
    finally:
        ai_request_finished()


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
        # Avoid a known retired Groq model even when an old Render env var remains.
        if model == "llama-3.3-70b-versatile":
            logger.debug("Skipping known retired Groq model: %s", model)
            continue
        # Retired/stale model names can remain in Render environment variables.
        # A 404 must be treated as configuration fallback, not as a request failure.
        try:
            # TPM limits are temporary. Retry once/twice using Groq's own retry
            # hint instead of failing the whole research request immediately.
            for attempt in range(3):
                try:
                    content=await _request(model,instruction)
                    return _parse_json(content)
                except GroqRateLimitError as e:
                    if attempt >= 2:
                        raise
                    delay=min(max(1.0,e.retry_after),20.0)
                    logger.warning("Groq TPM limit reached; retry %s/2 after %.1fs",attempt+1,delay)
                    await asyncio.sleep(delay)
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
Rules: infer the subject, geography, timeframe, and evidence needs only from the user's input and research questions. Select source types and domains dynamically for this specific task. Do not inject assumed topics, countries, institutions, parties, people, or events. Prefer the most authoritative sources appropriate to the subject, then corroborate with high-quality independent sources. Do not invent a source merely to fill a category.
Return ONLY JSON:
{{"context":"string","resource_types":["string"],"preferred_domains":["example.org"],"search_strategy":["string"],"rationale":"string"}}"""
    data = await groq_json(prompt)
    return data if isinstance(data, dict) else {}

async def breakdown_questions(text: str, prefs: dict) -> list[str]:
    prompt = f"""Build a concise, context-aware research plan for answering the user's input accurately.
USER INPUT: {text[:get_settings().groq_claim_input_chars]!r}
PREFERENCES: {json.dumps(prefs)}
Return the smallest useful set of specific, independently searchable questions whose answers together resolve the original input.
Rules:
- Preserve the exact subject and intent. Every question must help answer the input; do not drift into a general topic report.
- Infer relevant entities, relationships, geography, and timeframe from the input. Do not assume a country, institution, party, person, or topic that the input does not establish.
- For time-sensitive questions, verify the status as of the requested date/year; ask who currently holds the role or authority when relevant.
- For questions about leadership, government, organizations, offices, or control, distinguish the entity from the office/institution and identify the current holder or governing coalition when needed.
- When needed to establish a timeline, ask when the relevant election/appointment/decision occurred, who won or took office, the term's start/end or current status, and how long it has lasted. Calculate duration only from verified dates.
- For comparisons or causal questions, research the minimum facts needed to make the comparison or assess the cause.
- Use direct factual wording. Do not assume the answer or frame questions to support a preferred conclusion.
- Do not invent allegations, people, dates, locations, motives, or subclaims not present or logically necessary.
- Simple, stable questions may need only one question. If the answer depends on current status, a role/office, a governing or controlling entity, an election/appointment, or a timeline, return 3-5 targeted questions.
- For current-status questions, cover (when relevant): who/what holds the role or status now; the responsible party, coalition, organization, or authority; the most recent election/appointment/decision and its outcome; when the current term or status began; and the duration/status as of the requested date.
- Make each question independently searchable and include the key entity, place, and timeframe in its wording when known. Avoid vague questions such as "what happened?" or "what is the context?"
- Do not assume the answer. Questions must be neutral and able to establish a contrary answer.
- Order questions by importance; the first should establish the central fact needed to answer the user.
Return ONLY JSON: {{"questions":["string"]}}"""
    data = await groq_json(prompt)
    if not isinstance(data, dict): return []
    seen=set()
    questions=[]
    for item in data.get("questions",[]):
        question=str(item).strip()
        key=" ".join(question.lower().split())
        if question and key not in seen:
            seen.add(key)
            questions.append(question)
        if len(questions)>=5:break
    return questions


async def assess_research_evidence(input_text: str, questions: list[str], candidates: list[dict]) -> dict:
    """Semantically assess a bounded candidate pool and build question-answer evidence links."""
    # Keep the semantic pass small enough for free-tier TPM budgets.
    compact = [{
        "evidence_id": str(x.get("evidence_id", "")),
        "title": str(x.get("title", ""))[:180],
        "publisher": str(x.get("publisher", ""))[:80],
        "url": str(x.get("url", ""))[:280],
        "content": str(x.get("content", ""))[:600],
        "source_type": str(x.get("source_type", "Other")),
    } for x in candidates[:12]]
    prompt = f"""You are the evidence relevance and research-answer stage.
ORIGINAL USER QUESTION/CLAIM: {input_text[:1200]}
FRAMED RESEARCH QUESTIONS: {json.dumps(questions[:5], ensure_ascii=False)}
CANDIDATE WEB SOURCES: {json.dumps(compact, ensure_ascii=False)}
For each candidate, decide whether its title, snippet/content, publisher, and matched research question substantively help answer at least one framed research question. Judge meaning and context, not exact keyword overlap. A source that merely mentions the same word, a different country/jurisdiction, or a different entity is NOT relevant. Resolve ordinary shorthand and likely entity aliases from the user's own context (for example, a country's named political party when the question explicitly names that country); do not confuse similarly named entities in other jurisdictions.
Use only the supplied candidate metadata and content; do not use model memory to assert facts. Search snippets can be short, so do not require a long article excerpt. A clear, directly responsive headline/snippet can qualify as relevant, but label the answer partial if it does not establish the full answer. Do not reject a source merely because its wording differs from the research question.
Then build a concise answer to each research question using only relevant candidate evidence. Cite exact evidence IDs. If a source helps identify the relevant entity or directly answers part of a question, include it and mark the answer partial when necessary. If no supplied candidate helps answer a question, return an empty answer and empty evidence_ids. Never invent facts, quotations, dates, or source IDs.
Return ONLY JSON:
{{"relevant_evidence_ids":["E01"],"question_answers":[{{"question":"string","answer":"string","evidence_ids":["E01"],"status":"answered|partial|insufficient"}}]}}"""
    data = await groq_json(prompt)
    if not isinstance(data, dict):
        return {}
    valid_ids = {x["evidence_id"] for x in candidates if x.get("evidence_id")}
    data["relevant_evidence_ids"] = [x for x in data.get("relevant_evidence_ids", []) if x in valid_ids]
    for item in data.get("question_answers", []):
        if isinstance(item, dict):
            item["evidence_ids"] = [x for x in item.get("evidence_ids", []) if x in valid_ids]
    return data


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
