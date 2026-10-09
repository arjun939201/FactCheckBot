import json
import asyncio
import re
import math
from datetime import datetime,timezone
from pydantic import ValidationError
from ..models.factcheck import FactCheckResult,ArticleFactCheck,MediaAttachment
from ..config import get_settings
from .groq import groq_json,factcheck_instruction,decompose_claims,plan_resources,assess_research_evidence
from .search import search_web, SearchError, SearchRelevanceError
from .progress import update_progress

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
        # Even short questions may need contextual subquestions (current status,
        # responsible entity, dates, or duration). Keep the original question too;
        # the targeted subquestions improve retrieval without changing user intent.
        from .groq import breakdown_questions
        research_questions=await breakdown_questions(primary_text,prefs)
        if not research_questions:
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
    update_progress('researching')
    search_queries=list(research_questions)
    if explicit_question and primary_text not in search_queries:
        search_queries.insert(0, primary_text)
    batch_results = await asyncio.gather(*[
        search_web(q, resource_plan=resource_plan) for q in search_queries
    ], return_exceptions=True)
    update_progress('collecting')
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
    # Merge evidence across ALL research questions before applying the model
    # context limit. Query-order concatenation let the broad first search crowd
    # out stronger results discovered by targeted subquestions.
    evidence_by_url={}
    for question,batch in zip(search_queries,question_batches):
        for item in batch:
            url=item.get("url")
            if not url:
                continue
            candidate=dict(item)
            candidate["matched_questions"]=[question]
            existing=evidence_by_url.get(url)
            if existing is None:
                evidence_by_url[url]=candidate
                continue
            matched=list(dict.fromkeys(existing.get("matched_questions",[])+[question]))
            if (candidate.get("relevance_score",0),candidate.get("source_quality",0)) > (
                existing.get("relevance_score",0),existing.get("source_quality",0)
            ):
                evidence_by_url[url]=candidate
            evidence_by_url[url]["matched_questions"]=matched
            evidence_by_url[url]["relevant"]=bool(
                evidence_by_url[url].get("relevant") or candidate.get("relevant")
            )
    # Deduplicate exact/near-identical headlines across different search queries.
    # Keep the strongest record, while merging question coverage from duplicates.
    def _headline_key(value):
        words=re.findall(r"[a-z0-9]+",str(value).lower())
        stop={"the","a","an","and","or","of","for","to","in","on","at","by","from","with","list","latest","updated"}
        return " ".join(w for w in words if w not in stop)

    deduped={}
    for candidate in evidence_by_url.values():
        title_key=_headline_key(candidate.get("title",""))
        url_key=re.sub(r"[?#].*$","",str(candidate.get("url","")).rstrip("/")).lower()
        key=("title",title_key) if len(title_key)>=18 else ("url",url_key)
        existing=deduped.get(key)
        if existing is None:
            deduped[key]=candidate
            continue
        matched=list(dict.fromkeys(existing.get("matched_questions",[])+candidate.get("matched_questions",[])))
        winner=candidate if (
            candidate.get("source_quality",0),len(str(candidate.get("content",""))),candidate.get("relevance_score",0)
        ) > (
            existing.get("source_quality",0),len(str(existing.get("content",""))),existing.get("relevance_score",0)
        ) else existing
        winner["matched_questions"]=matched
        deduped[key]=winner

    evidence=sorted(
        deduped.values(),
        key=lambda x:(
            x.get("relevance_score",0),
            x.get("source_quality",0),
            len(str(x.get("content",""))),
        ),
        reverse=True,
    )
    # The search engine returns candidate pages. A bounded semantic pass decides
    # which sources answer the framed questions and builds evidence-linked answers.
    candidate_count=len(evidence)
    for i,item in enumerate(evidence,1):
        item["evidence_id"]=f"E{i:02d}"
    semantic_research = {}
    try:
        semantic_research = await assess_research_evidence(primary_text, research_questions, evidence)
    except Exception:
        # Keep the workflow available if the assessor is rate-limited; retain the
        # explicit relevance metadata for the final synthesis to judge cautiously.
        semantic_research = {}

    # Bounded sufficiency gate: if the first evidence pass leaves framed
    # questions unanswered, run one targeted repair pass for those gaps only.
    # This avoids both premature "UNVERIFIED" results and unbounded search loops.
    if isinstance(semantic_research, dict) and research_questions:
        answers = semantic_research.get("question_answers", [])
        answered = {
            " ".join(str(item.get("question", "")).lower().split())
            for item in answers
            if isinstance(item, dict)
            and str(item.get("status", "")).lower() == "answered"
            and str(item.get("answer", "")).strip()
            and item.get("evidence_ids")
        }
        missing_questions = [
            q for q in research_questions
            if " ".join(q.lower().split()) not in answered
        ][:3]
        if missing_questions:
            known_urls = {str(item.get("url", "")) for item in evidence}
            repair_batches = await asyncio.gather(*[
                search_web(q, resource_plan=resource_plan)
                for q in missing_questions
            ], return_exceptions=True)
            added = []
            next_id = len(evidence) + 1
            for batch in repair_batches:
                if isinstance(batch, Exception):
                    if isinstance(batch, SearchError):
                        provider_gap = True
                    continue
                for item in batch:
                    url = str(item.get("url", ""))
                    if not url or url in known_urls:
                        continue
                    known_urls.add(url)
                    candidate = dict(item)
                    candidate["evidence_id"] = f"E{next_id:02d}"
                    candidate["matched_questions"] = [
                        q for q in missing_questions
                        if q in str(candidate.get("title", "")) + " " + str(candidate.get("content", ""))
                    ]
                    added.append(candidate)
                    next_id += 1
            if added:
                evidence.extend(added)
                try:
                    repaired_research = await assess_research_evidence(
                        primary_text, research_questions, evidence
                    )
                    if isinstance(repaired_research, dict):
                        semantic_research = repaired_research
                except Exception:
                    # Preserve the first assessment if the repair assessment fails.
                    pass

    candidate_count = len(evidence)
    if semantic_research.get("relevant_evidence_ids"):
        relevant_ids=set(semantic_research["relevant_evidence_ids"])
        evidence=[x for x in evidence if x["evidence_id"] in relevant_ids]
        for item in evidence:
            item["relevant"]=True
            item["relevance_reason"]="Semantically matched to a framed research question"
    elif semantic_research:
        evidence=[]
    if not evidence:
        # Evidence gaps are valid research outcomes, not application crashes.
        if candidate_count and semantic_research:
            summary="Search retrieved pages, but none directly answered the framed research questions. No conclusion is asserted without relevant evidence."
            uncertainty="Pages were retrieved, but none passed semantic relevance assessment."
        elif provider_gap or not candidate_count:
            summary="Live search did not provide usable evidence. Search providers may be unavailable or timing out; retry the investigation."
            uncertainty="Search provider failure or timeout prevented evidence collection."
        else:
            summary="Available pages did not provide sufficiently relevant evidence to answer this investigation."
            uncertainty="Insufficient relevant evidence was retrieved."
        result={
            "claim":primary_text,"verdict":"UNVERIFIED","confidence":0,
            "summary":summary,
            "reasoning":"A reliable conclusion requires relevant, traceable evidence. The research process did not find enough evidence to support one.",
            "key_points":[],"supporting_evidence":[],"contradicting_evidence":[],
            "context":"","sources":[],"uncertainties":[uncertainty],
            "content_type":"QUESTION" if explicit_question else claims[0].get("content_type",prefs.get("content_mode","auto")),
            "report_title":"Media Fact Check Report" if media_only else "Fact Check Report",
            "report_sections":[],"attachments":[x.model_dump(mode="json") for x in attachments],
            "last_checked":datetime.now(timezone.utc).isoformat(),
            "live_evidence_available":bool(candidate_count),
            "claims_checked":[] if explicit_question else [{
                "claim":claims[0].get("claim",primary_text),
                "content_type":claims[0].get("content_type",prefs.get("content_mode","auto")),
                "verdict":"UNVERIFIED","confidence":0,"summary":summary,
                "supporting_evidence_ids":[],"contradicting_evidence_ids":[],
                "source_quality":0,"corroboration_count":0,
                "reasoning":"No source passed the evidence relevance gate.",
                "what_would_change_conclusion":"Relevant primary records or independent sources directly addressing the claim."
            }],
            "research_questions":research_questions,"research_data":[],"resource_plan":resource_plan,
        }
        return FactCheckResult.model_validate(result)

    # Keep the stable IDs assigned before semantic filtering.
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
    raw_items=list(semantic_research.get("question_answers", [])) if isinstance(semantic_research.get("question_answers", []), list) else []
    if not simple_question:
        from .groq import research_instruction
        raw_research = await groq_json(research_instruction(primary_text,research_questions,research_packets))
        extracted_items = raw_research.get("research_data",[]) if isinstance(raw_research,dict) else []
        if not isinstance(extracted_items,list):extracted_items=[]
        raw_items = raw_items + [x for x in extracted_items if isinstance(x,dict)]
        raw_items=raw_items[:8]

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
    # Prefer publisher diversity in the visible source list. Multiple URLs
    # from one publisher should not look like independent corroboration.
    selected_sources=[]
    seen_publishers=set()
    for item in sorted(source_pool,key=lambda x:(x.get("source_quality",0),x.get("relevance_score",0)),reverse=True):
        publisher=str(item.get("publisher","")).strip().lower()
        if publisher and publisher in seen_publishers:
            continue
        selected_sources.append(item)
        if publisher:seen_publishers.add(publisher)
        if len(selected_sources)>=8:break
    if len(selected_sources)<min(4,len(source_pool)):
        selected_urls={x.get("url") for x in selected_sources}
        for item in sorted(source_pool,key=lambda x:(x.get("source_quality",0),x.get("relevance_score",0)),reverse=True):
            if item.get("url") in selected_urls:continue
            selected_sources.append(item);selected_urls.add(item.get("url"))
            if len(selected_sources)>=8:break
    data["sources"]=[{"title":x["title"],"publisher":x["publisher"],"url":x["url"],"source_type":x["source_type"],"source_quality":x["source_quality"],"source_tier":x["source_tier"],"corroboration_count":len({str(y.get("publisher","")).strip().lower() for y in allowed_evidence if y.get("publisher") and str(y.get("publisher","")).strip().lower()!=str(x.get("publisher","")).strip().lower() and set(y.get("matched_questions",[])) & set(x.get("matched_questions",[])) and y.get("source_quality",0)>=60}),"relevance_score":x.get("relevance_score",0),"relevance_reason":x.get("relevance_reason","")} for x in selected_sources]
    # Expose question-level coverage so the UI/API can show which parts of the
    # investigation were answered, partially answered, or remain unresolved.
    data["question_coverage"]=[
        {"question":str(a.get("question","")),
         "status":str(a.get("status","insufficient")),
         "answer":str(a.get("answer","")),
         "evidence_ids":[eid for eid in a.get("evidence_ids",[]) if eid in by_id]}
        for a in raw_items if isinstance(a,dict) and a.get("question")
    ][:5]
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
        if not math.isfinite(number):
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
    update_progress('researching')
    evidence=await search_web(title+" "+article[:3000])
    update_progress('collecting')
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
        if not math.isfinite(number):return default
        if 0 <= number <= 1:number *= 100
        return max(0,min(100,int(round(number))))
    if "overall_confidence" in data:
        data["overall_confidence"]=_confidence_int(data["overall_confidence"])
    for item in data.get("claims_checked",[]):
        if isinstance(item,dict) and "confidence" in item:
            item["confidence"]=_confidence_int(item["confidence"])
    return ArticleFactCheck.model_validate(data)
