import json
import asyncio
import re
from datetime import datetime,timezone
from pydantic import ValidationError
from ..models.factcheck import FactCheckResult,ArticleFactCheck,MediaAttachment
from ..config import get_settings
from .groq import groq_json,factcheck_instruction,decompose_claims,plan_resources
from .search import search_web, SearchError, SearchRelevanceError

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
    # Simple questions are deterministic enough to avoid an AI decomposition call.
    # This materially reduces Groq TPM usage and preserves the exact user wording.
    simple_question = explicit_question and len(primary_text) <= 300 and not media_only
    if simple_question:
        claims=[{"claim":primary_text,"content_type":"QUESTION"}]
        research_questions=[primary_text]
    else:
        claims=await decompose_claims(primary_for_model,prefs,media_only=media_only)
        if explicit_question and claims:
            for claim in claims:
                if isinstance(claim,dict):
                    claim["content_type"]="QUESTION"
        if not claims:
            claims=[{"claim":primary_text,"content_type":prefs.get("content_mode","auto")}]
        claims=claims[:4]
        from .groq import breakdown_questions
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
    search_queries=list(research_questions)
    if explicit_question and primary_text not in search_queries:
        search_queries.insert(0, primary_text)
    batch_results = await asyncio.gather(*[
        search_web(q, resource_plan=resource_plan) for q in search_queries
    ], return_exceptions=True)
    question_batches=[]
    relevance_gap=False
    provider_gap=False
    for result in batch_results:
        if isinstance(result, SearchRelevanceError):
            relevance_gap=True
            question_batches.append([])
        elif isinstance(result, SearchError):
            provider_gap=True
            question_batches.append([])
        elif isinstance(result, Exception):
            # Do not silently misrepresent unexpected failures as weak evidence.
            raise result
        else:
            question_batches.append(result)
    evidence=[];seen=set()
    for batch in question_batches:
        for item in batch:
            if item["url"] not in seen:
                seen.add(item["url"])
                evidence.append(item)
    if not evidence:
        if relevance_gap:
            raise RuntimeError("Search returned pages, but none were sufficiently relevant to answer this input. Try a more specific query or add the key entity, location, or timeframe.")
        raise RuntimeError("Live search providers returned no usable results. Search may be temporarily unavailable; please retry.")

    # Give every retrieved source one stable ID before any AI research/synthesis step.
    for i,item in enumerate(evidence,1):
        item["evidence_id"]=f"E{i:02d}"
    evidence_by_url={item["url"]:item for item in evidence}

    research_packets=[]
    for question,batch in zip(search_queries,question_batches):
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

    # Raw research extraction is valuable for complex investigations, but a
    # simple question can go directly from retrieved evidence to synthesis.
    raw_items=[]
    if not simple_question:
        from .groq import research_instruction
        raw_research = await groq_json(research_instruction(primary_text,research_questions,research_packets))
        raw_items = raw_research.get("research_data",[]) if isinstance(raw_research,dict) else []
        if not isinstance(raw_items,list):raw_items=[]
        raw_items=[x for x in raw_items if isinstance(x,dict)][:5]

    evidence_for_model=[]
    excerpt_limit=min(get_settings().groq_evidence_excerpt_chars,900) if simple_question else get_settings().groq_evidence_excerpt_chars
    evidence_limit=8 if simple_question else 12
    for item in evidence[:evidence_limit]:
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
    allowed_evidence=(evidence if explicit_question else (strong_evidence or []))
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
        if explicit_question:return False
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
    if not valid_claims and not explicit_question:
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
    # Questions must never lose retrieved sources just because the synthesis
    # model forgot to populate its source array. If live evidence exists,
    # preserve the strongest retrieved sources deterministically.
    relevant_question_evidence=[x for x in allowed_evidence if x.get("relevant",False)]
    source_pool=(relevant_question_evidence or allowed_evidence) if explicit_question else [x for x in evidence if x["url"] in used]
    data["sources"]=[{"title":x["title"],"publisher":x["publisher"],"url":x["url"],"source_type":x["source_type"],"source_quality":x["source_quality"],"source_tier":x["source_tier"],"corroboration_count":sum(1 for y in allowed_evidence if y["publisher"]==x["publisher"]),"relevance_score":x.get("relevance_score",0),"relevance_reason":x.get("relevance_reason","")} for x in source_pool[:8]]
    data["live_evidence_available"]=bool(evidence)
    if explicit_question and not relevant_question_evidence:
        data["summary"]="Live sources were retrieved, but none were sufficiently relevant to establish the answer to this question."
        data.setdefault("uncertainties",[]).append("Live search succeeded, but no sufficiently relevant evidence was found.")
    # The user supplied text is the canonical subject of the report. Never let
    # media-derived wording replace the primary investigation title/claim.
    data["claim"]=primary_text if explicit_question else (claims[0]["claim"] if claims else primary_text)
    if explicit_question:
        data["content_type"]="QUESTION"
        # Compatibility placeholder; the UI renders questions as answers, not verdicts.
        data["verdict"]="UNVERIFIED"
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
        if not explicit_question:
            data.setdefault("uncertainties",[]).append("Retrieved sources did not support a grounded evidence mapping for the primary claim.")
    data["last_checked"]=datetime.now(timezone.utc).isoformat()

    # LLMs sometimes return confidence as a fraction (0.2) or decimal
    # percentage (72.5), while the API schema requires an integer 0-100.
    # Normalize confidence fields at the boundary rather than failing the request.
    def _confidence_int(value, default=0):
        try:
            number=float(value)
        except (TypeError,ValueError):
            return default
        if not (number == number):  # NaN
            return default
        if 0 <= number <= 1:
            number *= 100
        return max(0,min(100,int(round(number))))

    if "confidence" in data:
        data["confidence"]=_confidence_int(data["confidence"])
    for item in data.get("claims_checked",[]):
        if isinstance(item,dict) and "confidence" in item:
            item["confidence"]=_confidence_int(item["confidence"])

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
    def _confidence_int(value, default=0):
        try:number=float(value)
        except (TypeError,ValueError):return default
        if not (number == number):return default
        if 0 <= number <= 1:number *= 100
        return max(0,min(100,int(round(number))))
    if "overall_confidence" in data:
        data["overall_confidence"]=_confidence_int(data["overall_confidence"])
    for item in data.get("claims_checked",[]):
        if isinstance(item,dict) and "confidence" in item:
            item["confidence"]=_confidence_int(item["confidence"])
    return ArticleFactCheck.model_validate(data)
