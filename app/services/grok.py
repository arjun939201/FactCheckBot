import json,re,httpx
from ..config import get_settings
SYSTEM_PROMPT="""You are the analysis engine for a neutral evidence-first fact-checking application. Use ONLY the evidence supplied in the user message for evidence-based conclusions. Never invent sources, URLs, quotations, statistics, dates, studies, organizations, or evidence. If evidence is insufficient, use UNVERIFIED. Distinguish facts, uncertainty, opinions, predictions, and questions. Do not treat AI knowledge as independently verified evidence. Confidence is assessment confidence, not a mathematical probability. Return ONLY valid JSON."""
SCHEMA={"claim":"string","verdict":"TRUE|MOSTLY TRUE|PARTLY TRUE|MISLEADING|MOSTLY FALSE|FALSE|UNVERIFIED|OPINION|PREDICTION","confidence":0,"summary":"string","reasoning":"string","key_points":["string"],"supporting_evidence":[],"contradicting_evidence":[],"context":"string","sources":[],"uncertainties":[],"content_type":"FACTUAL CLAIM|OPINION|PREDICTION|QUESTION|SATIRE/UNCLEAR|MIXED","last_checked":"ISO datetime","claims_checked":[{"claim":"string","verdict":"...","confidence":0,"summary":"string"}]}
async def groq_json(instruction:str)->dict:
    s=get_settings()
    if not s.groq_api_key:raise RuntimeError("Groq API is not configured")
    async with httpx.AsyncClient(timeout=s.request_timeout) as c:
        r=await c.post("https://api.groq.com/openai/v1/chat/completions",headers={"Authorization":f"Bearer {s.groq_api_key}","Content-Type":"application/json"},
                       json={"model":s.groq_model,"temperature":0.1,"messages":[{"role":"system","content":SYSTEM_PROMPT},{"role":"user","content":instruction}]})
        r.raise_for_status(); content=r.json()["choices"][0]["message"]["content"]
    content=re.sub(r"^\`\`\`(?:json)?\s*|\s*\`\`\`$","",content.strip(),flags=re.I)
    return json.loads(content)
def factcheck_instruction(claim:str,evidence:list[dict],prefs:dict)->str:
    return f"""Fact-check this input: {claim!r}
Preferences: {json.dumps(prefs)}
Evidence retrieved by the application (the ONLY permitted external evidence):
{json.dumps(evidence,ensure_ascii=False)}
Return exactly one JSON object using this schema:
{json.dumps(SCHEMA)}
Split paragraphs into individual factual claims in claims_checked and provide an overall assessment. Source URLs must be copied exactly from supplied evidence. If no evidence exists, sources/evidence arrays must be empty and verdict should normally be UNVERIFIED unless clearly OPINION/PREDICTION. Never manufacture dates or excerpts."""
