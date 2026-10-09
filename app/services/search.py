import asyncio
import logging
import re
from urllib.parse import quote_plus,urlparse
from xml.etree import ElementTree as ET
from collections import Counter
import httpx
from .source_validator import source_type_for
from ..config import get_settings

logger=logging.getLogger(__name__)
class SearchError(Exception):pass
class SearchRelevanceError(SearchError):pass

def _quality(source_type,publisher,url):
    # Trust tier follows validated source classification, not publisher/headline text.
    # Search aggregators and misleading titles must never upgrade a source's authority.
    kind=str(source_type or "").lower()
    if kind=="government":return (95,"Primary/official")
    if kind in {"academic/scientific","academic"}:return (92,"Academic/official")
    if "fact-check" in kind:return (88,"Fact-checking")
    if "major news" in kind:return (82,"Major news")
    return (60,"Other")

def _canonical_url(value):
    """Canonicalize tracking variants without discarding meaningful query parameters."""
    from urllib.parse import parse_qsl, urlencode, urlunparse
    try:
        parsed=urlparse(str(value).strip())
        if parsed.scheme not in {"http","https"} or not parsed.netloc:return ""
        host=parsed.netloc.lower()
        if host.startswith("www."):host=host[4:]
        tracking_prefixes=("utm_",)
        tracking_names={"gclid","fbclid","mc_cid","mc_eid","ref","ref_src"}
        params=[
            (k,v) for k,v in parse_qsl(parsed.query,keep_blank_values=True)
            if not k.lower().startswith(tracking_prefixes) and k.lower() not in tracking_names
        ]
        return urlunparse((parsed.scheme.lower(),host,parsed.path.rstrip("/") or "/", "",urlencode(sorted(params)), ""))
    except Exception:
        return str(value).strip().rstrip("/")

def _normalise(results):
    out=[];seen=set()
    for x in results:
        if not isinstance(x,dict):continue
        u=x.get("href") or x.get("url") or ""
        canonical=_canonical_url(u)
        if not canonical or canonical in seen:continue
        seen.add(canonical);title=str(x.get("title",""))[:300]
        publisher=str(x.get("publisher") or urlparse(u).netloc)
        st=source_type_for(title,publisher,u);quality,tier=_quality(st,publisher,u)
        out.append({"title":title,"url":u,"content":str(x.get("body") or x.get("description") or "")[:5000],"publisher":publisher,"source_type":st,"source_quality":quality,"source_tier":tier})
    return out

def _ddgs_search(query,max_results,backend=None):
    from ddgs import DDGS
    kwargs={"max_results":max_results}
    if backend:kwargs["backend"]=backend
    return list(DDGS().text(query,**kwargs))

async def _ddgs(query,max_results):
    # Do not let a stalled search provider consume the entire request budget.
    timeout=max(3.0,float(get_settings().search_timeout))
    try:
        raw=await asyncio.wait_for(asyncio.to_thread(_ddgs_search,query,max_results),timeout=timeout)
        results=_normalise(raw)
        if results:return results
    except Exception as e:
        logger.warning("Primary web search failed: error=%s",type(e).__name__)
    for backend in ("google","bing"):
        try:
            raw=await asyncio.wait_for(asyncio.to_thread(_ddgs_search,query,max_results,backend),timeout=timeout)
            results=_normalise(raw)
            if results:return results
        except Exception as e:
            logger.warning("Fallback web search failed: backend=%s error=%s",backend,type(e).__name__)
    return []

async def _google_news(query,max_results):
    url="https://news.google.com/rss/search?q="+quote_plus(query)+"&hl=en-IN&gl=IN&ceid=IN:en"
    try:
        async with httpx.AsyncClient(timeout=get_settings().search_timeout,follow_redirects=True,headers={"User-Agent":"FactCheck/1.1"}) as client:
            r=await client.get(url);r.raise_for_status()
        root=ET.fromstring(r.text)
    except Exception as e:logger.warning("Google News RSS failed: error=%s",type(e).__name__);return []
    results=[]
    for item in root.findall(".//item")[:max_results]:
        source=item.findtext("source") or ""
        results.append({"title":item.findtext("title") or "","url":item.findtext("link") or "","publisher":source.strip(),"body":re.sub("<[^>]+>"," ",item.findtext("description") or "").strip()})
    return _normalise(results)

_STOPWORDS={
    "a","an","and","are","as","at","be","by","for","from","has","have",
    "in","is","it","of","on","or","that","the","their","this","to","was",
    "were","will","with","without","against","about","into","than","then","they",
    "them","there","these","those","what","when","where","which","who","why","how",
}

def _terms(value):
    return {t for t in re.findall(r"[a-z0-9]{3,}",value.lower()) if t not in _STOPWORDS}

def _relevance(query,item):
    q=" ".join(query.split()).lower()
    qt=_terms(q)
    hay=f"{item.get('title','')} {item.get('publisher','')} {item.get('content','')}".lower()
    ht=_terms(hay)
    if not qt:return 0
    overlap=qt & ht
    coverage=len(overlap)/len(qt)
    title_overlap=len(qt & _terms(str(item.get("title",""))))
    exact=1 if q in hay and len(q)>12 else 0
    score=min(100, round(coverage*65 + min(title_overlap,3)*8 + exact*25))
    required=1 if len(qt)<=2 else 2
    item["relevance_score"]=score
    item["relevance_reason"]=(
        "Exact phrase" if exact else
        f"{len(overlap)}/{len(qt)} key terms match; {title_overlap} in title"
    )
    # Broad body-text overlap alone is weak evidence. For multi-term queries,
    # require both a meaningful score and at least one matching title term;
    # this reduces results that mention keywords only incidentally.
    item["relevant"]=bool(
        exact or (
            len(overlap)>=required and
            title_overlap>=1 and
            score>=55
        )
    )
    return score

# This is a resource catalogue, not a topic-specific search script. The planner
# selects relevant categories for each request; unrelated categories are not forced.
_RESOURCE_DOMAINS = {
    "government/official":["gov.in","india.gov.in","pib.gov.in","pmo.gov.in","presidentofindia.gov.in","sansad.in","loksabha.nic.in","rajyasabha.nic.in"],
    "election authority":["eci.gov.in"],
    "courts/law":["sci.gov.in","main.sci.gov.in","indiacode.nic.in"],
    "legislation/regulations":["indiacode.nic.in","egazette.nic.in"],
    "academic/research":["pubmed.ncbi.nlm.nih.gov","nature.com","sciencedirect.com","arxiv.org"],
    "medical/health authorities":["who.int","cdc.gov","nih.gov","pubmed.ncbi.nlm.nih.gov"],
    "financial/regulatory":["rbi.org.in","sebi.gov.in","mca.gov.in"],
    "reputable news":["reuters.com","apnews.com","bbc.com"],
    "company/technical docs":["docs.github.com","developer.mozilla.org","python.org"],
    "standards/specifications":["ietf.org","w3.org","iso.org"],
    "security advisories":["cve.org","nvd.nist.gov","github.com"],
    "datasets/statistics":["data.gov.in","data.worldbank.org","imf.org"],
}
_KNOWN_PLANNER_DOMAINS={
    d for domains in _RESOURCE_DOMAINS.values() for d in domains
} | {"thehindu.com","timesofindia.com","indianexpress.com","reuters.com","apnews.com","bbc.com","factcheck.org"}

def _plan_queries(query, plan):
    terms=[f"{query} {str(x).strip()}"[:300] for x in plan.get("search_strategy",[])[:4] if str(x).strip()]
    domains=[]
    for d in plan.get("preferred_domains",[])[:8]:
        d=str(d).strip().lower().replace("https://","").replace("http://","").split("/")[0]
        if d in _KNOWN_PLANNER_DOMAINS:domains.append(d)
    for rtype in plan.get("resource_types",[])[:8]:
        domains.extend(_RESOURCE_DOMAINS.get(str(rtype).lower(),[]))
    domain_queries=[f"{query} site:{d}" for d in dict.fromkeys(domains)][:4]
    return list(dict.fromkeys(terms+domain_queries))

def _queries(query):
    # No hard-coded political, country, institution, or topic assumptions.
    # Query expansion comes from the context-aware resource planner below.
    q=" ".join(query.split())
    parts=[p.strip() for p in re.split(r"(?<=[.!?])\s+",q) if p.strip()]
    queries=[q]+parts[:1]
    if len(q)>180:queries.append(q[:180])
    return list(dict.fromkeys(queries))[:3]

async def search_web(query:str,max_results:int|None=None,resource_plan:dict|None=None)->list[dict]:
    max_results=max_results or get_settings().max_search_results
    max_results=max(2,min(max_results,30))
    queries=_queries(query)
    if resource_plan:
        queries=list(dict.fromkeys(queries+_plan_queries(query,resource_plan)))[:4]

    # Bound fan-out because multiple framed questions run concurrently.
    news_queries=queries[:2]
    news_batches=await asyncio.gather(*[_google_news(q,max_results) for q in news_queries],return_exceptions=True)
    news_results=[]
    for batch in news_batches:
        if isinstance(batch,list):news_results.extend(batch)

    ddgs_batches=await asyncio.gather(*[_ddgs(queries[0],min(max_results,6))],return_exceptions=True)
    ddgs_results=[]
    for batch in ddgs_batches:
        if isinstance(batch,list):ddgs_results.extend(batch)

    results=_normalise(news_results+ddgs_results)
    if not results:raise SearchError("No live web evidence was retrieved from available search providers")
    for item in results:_relevance(query,item)

    # Lexical overlap is only a retrieval hint. Keep a bounded candidate pool
    # for the question/evidence assessment stage; do not reject a page solely
    # because it uses different wording from the research question.
    ranked=sorted(results,key=lambda x:(x.get("relevance_score",0),x["source_quality"],len(x["content"])),reverse=True)
    seen_urls=set();seen_domains=set();out=[]
    for item in ranked:
        if item["url"] in seen_urls:continue
        domain=item["publisher"]
        if domain in seen_domains and item["source_quality"]<90:continue
        out.append(item);seen_urls.add(item["url"]);seen_domains.add(domain)
        if len(out)>=min(max_results,15):break
    counts=Counter(x["publisher"] for x in out)
    for i,x in enumerate(out,1):
        x["evidence_id"]=f"E{i:02d}";x["corroboration_count"]=counts[x["publisher"]]
    return out