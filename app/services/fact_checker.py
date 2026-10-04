from datetime import datetime,timezone
from pydantic import ValidationError
from ..models.factcheck import FactCheckResult,ArticleFactCheck
from .groq import groq_json,factcheck_instruction
from .search import search_web,SearchError
async def run_fact_check(text:str,prefs:dict)->FactCheckResult:
    evidence=[]; live=False
    try:
        evidence=await search_web(text)
        live=bool(evidence)
    except SearchError:
        live=False
    data=await groq_json(factcheck_instruction(text,evidence,prefs))
    known={x["url"] for x in evidence}
    for k in ("sources","supporting_evidence","contradicting_evidence"):
        data[k]=[x for x in data.get(k,[]) if x.get("url") in known]
    data["live_evidence_available"]=live
    if not live:
        data.setdefault("uncertainties",[]).append("Live web evidence retrieval is currently unavailable. This result should not be treated as independently verified.")
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
    evidence=[]; live=False
    try:
        evidence=await search_web(title+" "+article[:3000])
        live=bool(evidence)
    except SearchError:
        live=False
    prompt=f"""Article title: {title}
Article URL: {url}
Article text:
{article}
Identify important factual assertions and provide an article-level assessment. Use ONLY retrieved evidence. Return JSON with article_title, overall_verdict, overall_confidence, summary, claims_checked (claim, verdict, confidence, summary), sources, uncertainties, last_checked. Keep no more than 8 claims.
Retrieved evidence:
{evidence}"""
    data=await groq_json(prompt)
    known={x["url"] for x in evidence}
    data["article_url"]=url
    data["sources"]=[x for x in data.get("sources",[]) if x.get("url") in known]
    data["live_evidence_available"]=live
    if not live:
        data.setdefault("uncertainties",[]).append("Live web evidence retrieval is currently unavailable. This result should not be treated as independently verified.")
    data["last_checked"]=datetime.now(timezone.utc).isoformat()
    return ArticleFactCheck.model_validate(data)
