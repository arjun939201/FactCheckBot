import json
import asyncio
import re
from datetime import datetime,timezone
from pydantic import ValidationError
from ..models.factcheck import FactCheckResult,ArticleFactCheck,MediaAttachment
from ..config import get_settings
from .groq import groq_json,factcheck_instruction,decompose_claims,plan_resources
from .search import search_web

MAX_MEDIA_CONTEXT=12000

async def run_fact_check(text:str,prefs:dict,media_contexts:list|None=None,media_only:bool=False)->FactCheckResult:
    media_contexts=media_contexts or []
    attachments=[]
    media_blocks=[]
    for m in media_contexts:
        extracted=m.extracted_text[:6000]
        visual=m.visual_summary[:3000]
        if extracted:
            media_blocks.append(f"[{m.kind} OCR/transcript from {m.filename}]\n{extracted}")
        if visual:
            media_blocks.append(f"[{m.kind} visual context from {m.filename}]\n{visual}")
        attachments.append(MediaAttachment(
            filename=m.filename,media_type=m.media_type,kind=m.kind,size_bytes=m.size_bytes,
            extracted_text=extracted,visual_summary=visual
        ))

    primary_text=text.strip()
    media_only = bool(media_only or not primary_text)
    if media_only:
        primary_text=(
            "Analyze the attached media and identify the smallest set of substantive "
            "verifiable claims that the media itself presents. Do not create questions "
            "about identity, date, location, authenticity, or image description unless "
            "the user explicitly asks for them."
        )

    # IMPORTANT PRODUCT CONTRACT:
    # When the user supplies text, that text is the investigation subject. Media is
    # context/evidence, not a second prompt that should generate independent claims.
    primary_for_model=primary_text[:9000]
    # Explicit questions must remain questions, not be converted into factual claims.
    explicit_question = bool(re.search(r"\?\s*$", primary_text)) or bool(
        re.match(r"(?i)^(is|are|was|were|will|can|could|does|do|did|has|have|who|what|when|where|which|why|how)\b", primary_text)
    )
    claims=await decompose_claims(primary_for_model,prefs,media_only=media_only)
    if explicit_question and claims:
        for claim in claims:
            if isinstance(claim,dict):
                claim["content_type"]="QUESTION"
    if not claims:
        claims=[{"claim":primary_text,"content_type":prefs.get("content_mode","auto")}]
    claims=claims[:4]

    # Required workflow: input -> question breakdown -> web search -> raw research data -> synthesis/verdict.
    from .groq import breakdown_questions, research_instruction
    research_questions = await breakdown_questions(primary_text, prefs)
    if not research_questions:
        research_questions = [f"What evidence directly answers or verifies this input: {primary_text[:500]}?"]

    # Select evidence resources for this context before searching.
    try:
        resource_plan = await plan_resources(primary_text, research_questions, prefs)
    except Exception:
        # Resource planning must improve retrieval, never make the investigation unavailable.
        resource_plan = {}
    resource_plan.setdefault("context", "general")
    resource_plan.setdefault("resource_types", [])
    resource_plan.setdefault("preferred_domains", [])
    resource_plan.setdefault("search_strategy", [])
    resource_plan.setdefault("rationale", "")
    # Search each research question using the selected resource strategy.
    question_batches = await asyncio.gather(*[
        search_web(q, resource_plan=resource_plan) for q in research_questions
    ])
    evidence=[];seen=set()
    for batch in question_batches:
        for item in batch:
            if item["url"] not in seen:
                seen.add(item["url"])
                evidence.append(item)
    if not evidence:raise RuntimeError("Live web evidence retrieval returned no results")

    # Give every retrieved source one stable ID before any AI research/synthesis step.
    for i,item in enumerate(evidence,1):
        item["evidence_id"]=f"E{i:02d}"
    evidence_by_url={item["url"]:item for item in evidence}

    research_packets=[]
    for question,batch in zip(research_questions,question_batches):
        packet=[]
        for item in batch[:8]:
            source=evidence_by_url.get(item["url"])
            if not source:continue
            packet.append({
                "evidence_id": source["evidence_id"],
                "title": source.get("title",""),
                "publisher": source.get("publisher",""),
                "url": source["url"],
                "content": str(source.get("content",""))[:get_settings().groq_evidence_excerpt_chars],
                "source_quality": source.get("source_quality",0),
                "source_tier": source.get("source_tier","Other"),
                "relevance_score": source.get("relevance_score",0)
            })
        research_packets.append({"question":question,"web_results":packet})

    # Raw research answers are generated only from the retrieved web packets.
    raw_research = await groq_json(research_instruction(primary_text,research_questions,research_packets))
    raw_items = raw_research.get("research_data",[]) if isinstance(raw_research,dict) else []
    if not isinstance(raw_items,list):raw_items=[]
    raw_items=[x for x in raw_items if isinstance(x,dict)][:5]

    evidence_for_model=[]
    excerpt_limit=get_settings().groq_evidence_excerpt_chars
    for item in evidence[:12]:
        evidence_for_model.append({
            "evidence_id":item["evidence_id"],"title":item.get("title",""),
            "publisher":item.get("publisher",""),"url":item["url"],
            "content":str(item.get("content",""))[:excerpt_limit],
            "source_type":item.get("source_type","Other"),
            "source_quality":item.get("source_quality",0),
            "source_tier":item.get("source_tier","Other"),
            "relevance_score":item.get("relevance_score",0),
            "relevance_reason":item.get("relevance_reason",""),
        })

    media_context="\n\n".join(media_blocks)[:MAX_MEDIA_CONTEXT]
    synthesis_input = factcheck_instruction(
        primary_for_model,evidence_for_model,prefs,claims,
        media_context=media_context,media_only=media_only
    ) + f"""\nRESEARCH QUESTIONS:
{json.dumps(research_questions,ensure_ascii=False)}
RAW RESEARCH DATA:
{json.dumps(raw_items,ensure_ascii=False)}
FINAL STAGE: Synthesize the research into the best-supported answer to the user's original input, then assign the verdict. Do not use unsupported model knowledge."""
    data=await groq_json(synthesis_input)
    if not isinstance(data,dict):
        raise RuntimeError("The AI returned an invalid fact-check object")

    def _object_list(value):
        if not isinstance(value,list):return []
        return [x if isinstance(x,dict) else {"url":str(x)} for x in value]

    for x in evidence:
        x["source_tier"]=str(x.get("source_tier","Other"))
        try:x["source_quality"]=max(0,min(100,int(x.get("source_quality",0))))
        except (TypeError,ValueError):x["source_quality"]=0
    strong_evidence=[x for x in evidence if x.get("relevant",True)]
    allowed_evidence=strong_evidence or []
    known={x["url"] for x in allowed_evidence};by_id={x["evidence_id"]:x for x in allowed_evidence}
    for k in ("sources","supporting_evidence","contradicting_evidence"):
        data[k]=[x for x in _object_list(data.get(k,[])) if x.get("url") in known]

    # The model may still try to turn media observations into new fact checks.
    # Reject those when text is the primary subject.
    def tokens(value):
        return set(re.findall(r"[a-z0-9]+",str(value).lower()))
    primary_tokens=[tokens(c.get("claim","")) for c in claims]
    def relevant_claim(c):
        if not isinstance(c,dict) or not str(c.get("claim","")).strip():return False
        if media_only:return str(c.get("content_type","")).upper()!="QUESTION"
        ct=str(c.get("content_type","")).upper()
        if ct=="QUESTION":return False
        ctoks=tokens(c.get("claim",""))
        return any(len(ctoks & p)>=max(2,min(5,len(p)//3+1)) for p in primary_tokens)

    valid_claims=[]
    for c in _object_list(data.get("claims_checked",[])):
        if not relevant_claim(c):continue
        sids=[x for x in c.get("supporting_evidence_ids",[]) if x in by_id]
        cids=[x for x in c.get("contradicting_evidence_ids",[]) if x in by_id]
        refs=[by_id[x] for x in dict.fromkeys(sids+cids)]
        c["supporting_evidence_ids"]=sids;c["contradicting_evidence_ids"]=cids
        c["source_quality"]=round(sum(x["source_quality"] for x in refs)/len(refs)) if refs else 0
        c["corroboration_count"]=len({x["publisher"] for x in refs})
        valid_claims.append(c)
    if not valid_claims:
        # Never let an image create an empty/divided report when the user supplied text.
        valid_claims=[{
            "claim":claims[0]["claim"],
            "content_type":claims[0].get("content_type",prefs.get("content_mode","auto")),
            "verdict":"UNVERIFIED","confidence":0,
            "summary":"The primary claim could not be grounded in sufficiently relevant evidence.",
            "supporting_evidence_ids":[],"contradicting_evidence_ids":[],
            "source_quality":0,"corroboration_count":0,
            "reasoning":"Retrieved material did not provide relevant evidence for the primary claim.",
            "what_would_change_conclusion":"A relevant primary source or independent reporting directly addressing the claim."
        }]
    data["claims_checked"]=valid_claims[:4]

    mapped_support=[];mapped_contra=[]
    for c in data["claims_checked"]:
        for eid in c["supporting_evidence_ids"]:
            x=by_id[eid];mapped_support.append({"evidence_id":eid,"claim":c["claim"],"excerpt":x["content"],"url":x["url"],"title":x["title"],"publisher":x["publisher"],"source_type":x["source_type"],"source_quality":x["source_quality"],"source_tier":x["source_tier"],"relevance_score":x.get("relevance_score",0),"relevance_reason":x.get("relevance_reason","")})
        for eid in c["contradicting_evidence_ids"]:
            x=by_id[eid];mapped_contra.append({"evidence_id":eid,"claim":c["claim"],"excerpt":x["content"],"url":x["url"],"title":x["title"],"publisher":x["publisher"],"source_type":x["source_type"],"source_quality":x["source_quality"],"source_tier":x["source_tier"],"relevance_score":x.get("relevance_score",0),"relevance_reason":x.get("relevance_reason","")})
    data["supporting_evidence"]=mapped_support;data["contradicting_evidence"]=mapped_contra
    used={x["url"] for x in mapped_support+mapped_contra}
    data["sources"]=[{"title":x["title"],"publisher":x["publisher"],"url":x["url"],"source_type":x["source_type"],"source_quality":x["source_quality"],"source_tier":x["source_tier"],"corroboration_count":sum(1 for y in allowed_evidence if y["publisher"]==x["publisher"]),"relevance_score":x.get("relevance_score",0),"relevance_reason":x.get("relevance_reason","")} for x in evidence if x["url"] in used][:12]
    data["live_evidence_available"]=bool(evidence)
    # The user supplied text is the canonical subject of the report. Never let
    # media-derived wording replace the primary investigation title/claim.
    data["claim"]=claims[0]["claim"] if claims else primary_text
    if explicit_question:
        data["content_type"]="QUESTION"
        # A question receives an answer, not a truth-status verdict.
        data["verdict"]="UNVERIFIED"
        data["confidence"]=0
    data["report_title"]="Fact Check Report" if not media_only else "Media Fact Check Report"
    data["attachments"]=[x.model_dump(mode="json") for x in attachments]
    valid_ids={x["evidence_id"] for x in evidence}
    cleaned_research=[]
    for x in raw_items:
        q=str(x.get("question","")).strip()
        a=str(x.get("answer","")).strip()
        ids=[i for i in x.get("evidence_ids",[]) if i in valid_ids]
        if q and a: cleaned_research.append({"question":q,"answer":a,"evidence_ids":ids})
    data["research_questions"]=research_questions
    data["research_data"]=cleaned_research
    data["resource_plan"]=resource_plan
    if not data["sources"] and data.get("verdict") not in {"OPINION","PREDICTION"}:
        data["verdict"]="UNVERIFIED";data["confidence"]=min(int(data.get("confidence",0)),50)
        data.setdefault("uncertainties",[]).append("Retrieved sources did not support a grounded evidence mapping for the primary claim.")
    data["last_checked"]=datetime.now(timezone.utc).isoformat()
    try:return FactCheckResult.model_validate(data)
    except ValidationError as e:raise RuntimeError("The AI returned an invalid fact-check result") from e

async def run_url_fact_check(url:str,prefs:dict)->ArticleFactCheck:
    from .article_parser import fetch_article,ArticleFetchError
    try:title,article=await fetch_article(url)
    except ArticleFetchError as e:raise ValueError(str(e)) from e
    evidence=await search_web(title+" "+article[:3000])
    if not evidence:raise RuntimeError("Live web evidence retrieval returned no results")
    excerpt_limit=get_settings().groq_evidence_excerpt_chars
    compact_evidence=[{
        "evidence_id":x.get("evidence_id",""),
        "title":x.get("title",""),
        "publisher":x.get("publisher",""),
        "url":x.get("url",""),
        "content":str(x.get("content",""))[:excerpt_limit],
        "source_quality":x.get("source_quality",0),
        "source_tier":x.get("source_tier","Other"),
    } for x in evidence[:12]]
    prompt=f"""Article title: {title[:500]}
Article URL: {url}
Article text:
{article[:9000]}
Identify important factual assertions and provide an article-level assessment. Use ONLY retrieved evidence. Return JSON with article_title, overall_verdict, overall_confidence, summary, claims_checked (claim, verdict, confidence, summary), sources, uncertainties, last_checked. Keep no more than 8 claims.
Retrieved evidence:
{json.dumps(compact_evidence,ensure_ascii=False)}"""
    data=await groq_json(prompt)
    if not isinstance(data,dict):
        raise RuntimeError("The AI returned an invalid article fact-check object")
    known={x["url"] for x in evidence}
    raw_sources=data.get("sources",[])
    if not isinstance(raw_sources,list): raw_sources=[]
    raw_sources=[x if isinstance(x,dict) else {"url":str(x)} for x in raw_sources]
    data["article_url"]=url;data["sources"]=[x for x in raw_sources if x.get("url") in known];data["live_evidence_available"]=True;data["last_checked"]=datetime.now(timezone.utc).isoformat()
    return ArticleFactCheck.model_validate(data)
