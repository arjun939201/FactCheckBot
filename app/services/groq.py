import json,re,logging,httpx
from ..config import get_settings

logger=logging.getLogger(__name__)
SYSTEM_PROMPT="""You are the analysis engine for a neutral evidence-first fact-checking application. Use ONLY the evidence supplied in the user message for evidence-based conclusions. Never invent sources, URLs, quotations, statistics, dates, studies, organizations, or evidence. If evidence is insufficient, use UNVERIFIED. Distinguish facts, uncertainty, opinions, predictions, and questions. Do not treat AI knowledge as independently verified evidence. Confidence is assessment confidence, not a mathematical probability. Return ONLY valid JSON."""
SCHEMA={"claim":"string","verdict":"TRUE|MOSTLY TRUE|PARTLY TRUE|MISLEADING|MOSTLY FALSE|FALSE|UNVERIFIED|OPINION|PREDICTION","confidence":0,"summary":"string","reasoning":"string","key_points":["string"],"supporting_evidence":[],"contradicting_evidence":[],"context":"string","sources":[],"uncertainties":[],"content_type":"FACTUAL CLAIM|OPINION|PREDICTION|QUESTION|SATIRE/UNCLEAR|MIXED","last_checked":"ISO datetime","claims_checked":[{"claim":"string","verdict":"...","confidence":0,"summary":"string"}]}

async def _request(model:str,instruction:str)->str:
    s=get_settings()
    async with httpx.AsyncClient(timeout=s.request_timeout) as c:
        r=await c.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization":f"Bearer {s.groq_api_key}","Content-Type":"application/json"},
            json={"model":model,"temperature":0.1,"messages":[{"role":"system","content":SYSTEM_PROMPT},{"role":"user","content":instruction}]},
        )
        if r.is_error:
            logger.error("Groq request failed: status=%s model=%s body=%s",r.status_code,model,r.text[:1000])
            r.raise_for_status()
        payload=r.json()
        return payload["choices"][0]["message"]["content"]

async def groq_json(instruction:str)->dict:
    s=get_settings()
    if not s.groq_api_key:
        raise RuntimeError("Groq API is not configured")
    try:
        try:
            content=await _request(s.groq_model,instruction)
        except httpx.HTTPStatusError as e:
            if e.response.status_code!=404 or not s.groq_fallback_model or s.groq_fallback_model==s.groq_model:
                raise
            logger.warning("Configured Groq model unavailable; retrying with fallback model=%s",s.groq_fallback_model)
            content=await _request(s.groq_fallback_model,instruction)
    except httpx.HTTPError:
        logger.exception("Groq HTTP request failed")
        raise
    except (KeyError,TypeError,ValueError):
        logger.exception("Groq returned an unexpected response")
        raise RuntimeError("Groq returned an unexpected response")
    content=re.sub(r"^```(?:json)?\s*|\s*```$","",content.strip(),flags=re.I)
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        logger.error("Groq returned invalid JSON: content_prefix=%s",content[:1000])
        raise RuntimeError("Groq returned invalid JSON")

def factcheck_instruction(claim:str,evidence:list[dict],prefs:dict)->str:
    return f"""Fact-check this input: {claim!r}
Preferences: {json.dumps(prefs)}
Evidence retrieved by the application (the ONLY permitted external evidence):
{json.dumps(evidence,ensure_ascii=False)}
Return exactly one JSON object using this schema:
{json.dumps(SCHEMA)}
Split paragraphs into individual factual claims in claims_checked and provide an overall assessment. Source URLs must be copied exactly from supplied evidence. If no evidence exists, sources/evidence arrays must be empty and verdict should normally be UNVERIFIED unless clearly OPINION/PREDICTION. Never manufacture dates or excerpts."""
