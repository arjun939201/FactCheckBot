import asyncio
from datetime import datetime,timezone
from pydantic import ValidationError
from ..models.factcheck import FactCheckResult,ArticleFactCheck,MediaAttachment
from .groq import groq_json,factcheck_instruction,decompose_claims
from .search import search_web

MAX_MEDIA_CONTEXT=16000

async def run_fact_check(text:str,prefs:dict,media_contexts:list|None=None)->FactCheckResult:
    media_contexts=media_contexts or [];media_text=[];attachments=[]
    for m in media_contexts:
        extracted=m.extracted_text[:6000];visual=m.visual_summary[:3000]
        if extracted:media_text.append("["+m.kind+" transcript/OCR from "+m.filename+"]\n"+extracted)
        if visual:media_text.append("["+m.kind+" visual context from "+m.filename+"]\n"+visual)
        attachments.append(MediaAttachment(filename=m.filename,media_type=m.media_type,kind=m.kind,size_bytes=m.size_bytes,extracted_text=extracted,visual_summary=visual))
    research_text=text.strip()
    if media_text:research_text+="\n\nMEDIA-DERIVED CONTENT:\n"+"\n\n".join(media_text)
    research_text=research_text[:MAX_MEDIA_CONTEXT]
    claims=await decompose_claims(research_text,prefs)
    if not claims:claims=[{"claim":research_text,"content_type":prefs.get("content_mode","auto")}]
    batches=await asyncio.gather(*[search_web(c["claim"]) for c in claims])
    evidence=[];seen=set()
    for batch in batches:
        for item in batch:
            if item["url"] not in seen:seen.add(item["url"]);evidence.append(item)
    if not evidence:raise RuntimeError("Live web evidence retrieval returned no results")
    for i,item in enumerate(evidence,1):item["evidence_id"]=f"E{i:02d}"
    data=await groq_json(factcheck_instruction(research_text,evidence,prefs,claims))
    if not isinstance(data,dict):
        raise RuntimeError("The AI returned an invalid fact-check object")

    # Models sometimes return URL strings instead of the documented evidence
    # objects. Normalize at the boundary so malformed-but-recoverable output
    # cannot crash the request with AttributeError.
    def _object_list(value):
        if not isinstance(value,list):
            return []
        return [x if isinstance(x,dict) else {"url":str(x)} for x in value]

    for x in evidence:
        x["source_tier"] = str(x.get("source_tier", "Other"))
        try: x["source_quality"] = max(0, min(100, int(x.get("source_quality", 0))))
        except (TypeError, ValueError): x["source_quality"] = 0
    known={x["url"] for x in evidence};by_id={x["evidence_id"]:x for x in evidence}
    for k in ("sources","supporting_evidence","contradicting_evidence"):
        data[k]=[x for x in _object_list(data.get(k,[])) if x.get("url") in known]
    valid_claims=[]
    for c in _object_list(data.get("claims_checked",[])):
        sids=[x for x in c.get("supporting_evidence_ids",[]) if x in by_id]
        cids=[x for x in c.get("contradicting_evidence_ids",[]) if x in by_id]
        refs=[by_id[x] for x in dict.fromkeys(sids+cids)]
        c["supporting_evidence_ids"]=sids;c["contradicting_evidence_ids"]=cids
        c["source_quality"]=round(sum(x["source_quality"] for x in refs)/len(refs)) if refs else 0
        c["corroboration_count"]=len({x["publisher"] for x in refs})
        valid_claims.append(c)
    data["claims_checked"]=valid_claims
    mapped_support=[];mapped_contra=[]
    for c in valid_claims:
        for eid in c["supporting_evidence_ids"]:
            x=by_id[eid];mapped_support.append({"evidence_id":eid,"claim":c["claim"],"excerpt":x["content"],"url":x["url"],"title":x["title"],"publisher":x["publisher"],"source_type":x["source_type"],"source_quality":x["source_quality"],"source_tier":x["source_tier"]})
        for eid in c["contradicting_evidence_ids"]:
            x=by_id[eid];mapped_contra.append({"evidence_id":eid,"claim":c["claim"],"excerpt":x["content"],"url":x["url"],"title":x["title"],"publisher":x["publisher"],"source_type":x["source_type"],"source_quality":x["source_quality"],"source_tier":x["source_tier"]})
    data["supporting_evidence"]=mapped_support;data["contradicting_evidence"]=mapped_contra
    used={x["url"] for x in mapped_support+mapped_contra}
    data["sources"]=[{"title":x["title"],"publisher":x["publisher"],"url":x["url"],"source_type":x["source_type"],"source_quality":x["source_quality"],"source_tier":x["source_tier"],"corroboration_count":sum(1 for y in evidence if y["publisher"]==x["publisher"])} for x in evidence if x["url"] in used][:12]
    data["live_evidence_available"]=bool(evidence);data["attachments"]=[x.model_dump(mode="json") for x in attachments]
    if not data["sources"] and data.get("verdict") not in {"OPINION","PREDICTION"}:
        data["verdict"]="UNVERIFIED";data["confidence"]=min(int(data.get("confidence",0)),50)
        data.setdefault("uncertainties",[]).append("Retrieved sources did not support a grounded evidence mapping.")
    data["last_checked"]=datetime.now(timezone.utc).isoformat()
    try:return FactCheckResult.model_validate(data)
    except ValidationError as e:raise RuntimeError("The AI returned an invalid fact-check result") from e

async def run_url_fact_check(url:str,prefs:dict)->ArticleFactCheck:
    from .article_parser import fetch_article,ArticleFetchError
    try:title,article=await fetch_article(url)
    except ArticleFetchError as e:raise ValueError(str(e)) from e
    evidence=await search_web(title+" "+article[:3000])
    if not evidence:raise RuntimeError("Live web evidence retrieval returned no results")
    prompt=f"""Article title: {title}
Article URL: {url}
Article text:
{article}
Identify important factual assertions and provide an article-level assessment. Use ONLY retrieved evidence. Return JSON with article_title, overall_verdict, overall_confidence, summary, claims_checked (claim, verdict, confidence, summary), sources, uncertainties, last_checked. Keep no more than 8 claims.
Retrieved evidence:
{evidence}"""
    data=await groq_json(prompt)
    if not isinstance(data,dict):
        raise RuntimeError("The AI returned an invalid article fact-check object")
    known={x["url"] for x in evidence}
    raw_sources=data.get("sources",[])
    if not isinstance(raw_sources,list): raw_sources=[]
    raw_sources=[x if isinstance(x,dict) else {"url":str(x)} for x in raw_sources]
    data["article_url"]=url;data["sources"]=[x for x in raw_sources if x.get("url") in known];data["live_evidence_available"]=True;data["last_checked"]=datetime.now(timezone.utc).isoformat()
    return ArticleFactCheck.model_validate(data)
