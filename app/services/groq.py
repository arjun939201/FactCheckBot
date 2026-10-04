import json,re,logging,httpx
from ..config import get_settings

logger=logging.getLogger(__name__)
SYSTEM_PROMPT="""You are the analysis engine for a neutral evidence-first fact-checking application. Use ONLY the evidence supplied in the user message for evidence-based conclusions. Never invent sources, URLs, quotations, statistics, dates, studies, organizations, or evidence. If evidence is insufficient, use UNVERIFIED. Distinguish facts, uncertainty, opinions, predictions, and questions. Do not treat AI knowledge as independently verified evidence. Confidence is assessment confidence, not a mathematical probability. A statement about a person's or group's alleged motive, conduct, propaganda, discrimination, or political behavior is still a FACTUAL CLAIM when it asserts that something happened or is happening; do not label it OPINION merely because it concerns motives or politics. Reserve OPINION for subjective judgments, preferences, beliefs, or value statements that do not assert a verifiable event or state of affairs. Return ONLY valid JSON."""
SCHEMA={"claim":"string","verdict":"TRUE|MOSTLY TRUE|PARTLY TRUE|MISLEADING|MOSTLY FALSE|FALSE|UNVERIFIED|OPINION|PREDICTION","confidence":0,"summary":"string","reasoning":"string","key_points":["string"],"supporting_evidence":[],"contradicting_evidence":[],"context":"string","sources":[],"uncertainties":[],"content_type":"FACTUAL CLAIM|ARGUMENT|OPINION|PROPAGANDA|PREDICTION|QUESTION|SATIRE/UNCLEAR|MIXED","report_title":"string","report_sections":["string"],"claims_checked":[{"claim":"string","content_type":"FACTUAL CLAIM|ARGUMENT|OPINION|PROPAGANDA|PREDICTION|QUESTION|SATIRE/UNCLEAR|MIXED","verdict":"...","confidence":0,"summary":"string","supporting_evidence_ids":["E01"],"contradicting_evidence_ids":["E02"],"source_quality":0,"corroboration_count":0,"reasoning":"string","what_would_change_conclusion":"string"}]}

async def _request(model,instruction):
    s=get_settings()
    async with httpx.AsyncClient(timeout=s.request_timeout) as c:
        r=await c.post("https://api.groq.com/openai/v1/chat/completions",headers={"Authorization":f"Bearer {s.groq_api_key}","Content-Type":"application/json"},json={"model":model,"temperature":0.1,"messages":[{"role":"system","content":SYSTEM_PROMPT},{"role":"user","content":instruction}]})
        if r.is_error:
            logger.error("Groq request failed: status=%s model=%s body=%s",r.status_code,model,r.text[:1000]);r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

async def groq_json(instruction):
    s=get_settings()
    if not s.groq_api_key:raise RuntimeError("Groq API is not configured")
    try:
        try:content=await _request(s.groq_model,instruction)
        except httpx.HTTPStatusError as e:
            if e.response.status_code!=404 or not s.groq_fallback_model or s.groq_fallback_model==s.groq_model:raise
            logger.warning("Configured Groq model unavailable; retrying with fallback model=%s",s.groq_fallback_model);content=await _request(s.groq_fallback_model,instruction)
    except httpx.HTTPError:
        logger.exception("Groq HTTP request failed");raise
    except (KeyError,TypeError,ValueError):
        logger.exception("Groq returned an unexpected response");raise RuntimeError("Groq returned an unexpected response")
    content=re.sub(r"^\`\`\`(?:json)?\s*|\s*\`\`\`$","",content.strip(),flags=re.I)
    try:return json.loads(content)
    except json.JSONDecodeError:
        logger.error("Groq returned invalid JSON: content_prefix=%s",content[:1000]);raise RuntimeError("Groq returned invalid JSON")

async def decompose_claims(text:str,prefs:dict)->list[dict]:
    prompt=f"""Decompose this input into the smallest meaningful components that should be analyzed separately.
Input: {text!r}
Preferences: {json.dumps(prefs)}
Return ONLY JSON: {{"claims":[{{"claim":"string","content_type":"FACTUAL CLAIM|ARGUMENT|OPINION|PROPAGANDA|PREDICTION|QUESTION|SATIRE/UNCLEAR|MIXED"}}]}}
Rules: preserve the user's meaning; do not fact-check or assign truth; do not invent claims; combine only inseparable fragments; split multiple factual assertions and separate factual assertions from opinions or conclusions. Maximum 8 components."""
    data=await groq_json(prompt);items=data.get("claims",[])
    return [x for x in items if isinstance(x,dict) and str(x.get("claim","")).strip()][:8]

def factcheck_instruction(claim,evidence,prefs):
    return f"""Fact-check this input: {claim!r}
Preferences: {json.dumps(prefs)}
Retrieved evidence (ONLY permitted external evidence):
{json.dumps(evidence,ensure_ascii=False)}
For each decomposed component, return claims_checked with exact evidence IDs from the supplied evidence. Do not invent IDs. Use supporting_evidence_ids and contradicting_evidence_ids to map evidence to that claim. source_quality must reflect the retrieved source quality (0-100), and corroboration_count is the number of distinct retrieved sources that independently support the assessment. For ARGUMENT, analyze premises, conclusion, reasoning gaps and evidence. For OPINION, analyze the viewpoint without pretending a subjective preference can be objectively proven. For PROPAGANDA, identify concrete persuasive techniques only when actually present; political content is not automatically propaganda. For FACTUAL CLAIM, assess verifiable assertions. For QUESTION, explain what would need to be established. For MIXED, separate components.
Return exactly one JSON object using this schema:
{json.dumps(SCHEMA)}
Sources/evidence URLs and IDs must be copied exactly from supplied evidence. If evidence is insufficient, use UNVERIFIED. Never manufacture dates, excerpts, sources, IDs, or facts."""
