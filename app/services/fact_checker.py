from datetime import datetime,timezone
from pydantic import ValidationError
from ..models.factcheck import FactCheckResult,ArticleFactCheck,MediaAttachment
from .groq import groq_json,factcheck_instruction
from .search import search_web

MAX_MEDIA_CONTEXT=16000

async def run_fact_check(text:str,prefs:dict,media_contexts:list|None=None)->FactCheckResult:
    media_contexts=media_contexts or []
    media_text=[];attachments=[]
    for m in media_contexts:
        extracted=m.extracted_text[:6000]; visual=m.visual_summary[:3000]
        if extracted: media_text.append("["+m.kind+" transcript/OCR from "+m.filename+"]\n"+extracted)
        if visual: media_text.append("["+m.kind+" visual context from "+m.filename+"]\n"+visual)
        attachments.append(MediaAttachment(filename=m.filename,media_type=m.media_type,kind=m.kind,size_bytes=m.size_bytes,extracted_text=extracted,visual_summary=visual))
    research_text=text.strip()
    if media_text: research_text+="\n\nMEDIA-DERIVED CONTENT:\n"+"\n\n".join(media_text)
    research_text=research_text[:MAX_MEDIA_CONTEXT]
    evidence=await search_web(research_text)
    data=await groq_json(factcheck_instruction(research_text,evidence,prefs))
    known={x["url"] for x in evidence}
    for k in ("sources","supporting_evidence","contradicting_evidence"):
        data[k]=[x for x in data.get(k,[]) if x.get("url") in known]
    data["live_evidence_available"]=bool(evidence);data["attachments"]=[x.model_dump(mode="json") for x in attachments]
    if not evidence: raise RuntimeError("Live web evidence retrieval returned no results")
    if not data.get("sources") and data.get("verdict") not in {"OPINION","PREDICTION"}:
        data["verdict"]="UNVERIFIED";data["confidence"]=min(int(data.get("confidence",0)),50)
        data.setdefault("uncertainties",[]).append("Insufficient reliable evidence was found to independently verify this claim.")
    data["last_checked"]=datetime.now(timezone.utc).isoformat()
    try:return FactCheckResult.model_validate(data)
    except ValidationError as e:raise RuntimeError("The AI returned an invalid fact-check result") from e

async def run_url_fact_check(url:str,prefs:dict)->ArticleFactCheck:
    from .article_parser import fetch_article,ArticleFetchError
    try:title,article=await fetch_article(url)
    except ArticleFetchError as e:raise ValueError(str(e)) from e
    evidence=await search_web(title+" "+article[:3000])
    if not evidence: raise RuntimeError("Live web evidence retrieval returned no results")
    prompt=f"""Article title: {title}
Article URL: {url}
Article text:
{article}
Identify important factual assertions and provide an article-level assessment. Use ONLY retrieved evidence. Return JSON with article_title, overall_verdict, overall_confidence, summary, claims_checked (claim, verdict, confidence, summary), sources, uncertainties, last_checked. Keep no more than 8 claims.
Retrieved evidence:
{evidence}"""
    data=await groq_json(prompt)
    known={x["url"] for x in evidence}
    data["article_url"]=url;data["sources"]=[x for x in data.get("sources",[]) if x.get("url") in known]
    data["live_evidence_available"]=True;data["last_checked"]=datetime.now(timezone.utc).isoformat()
    return ArticleFactCheck.model_validate(data)
