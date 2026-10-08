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

def _quality(source_type,publisher,url):
    s=f"{source_type} {publisher} {url}".lower()
    if "government" in source_type.lower() or ".gov" in s:return (95,"Primary/official")
    if "academic" in source_type.lower() or any(x in s for x in ["who.int","un.org","imf.org","worldbank.org","nature.com","science.org","pubmed","arxiv.org"]):return (92,"Academic/official")
    if "fact-check" in source_type.lower() or "factcheck" in s:return (88,"Fact-checking")
    if "major news" in source_type.lower() or any(x in s for x in ["reuters","apnews","bbc","theguardian","nytimes","washingtonpost"]):return (82,"Major news")
    return (60,"Other")

def _normalise(results):
    out=[];seen=set()
    for x in results:
        if not isinstance(x,dict):continue
        u=x.get("href") or x.get("url") or ""
        if urlparse(u).scheme not in {"http","https"} or u in seen:continue
        seen.add(u);title=str(x.get("title",""))[:300]
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
    try:return _normalise(await asyncio.to_thread(_ddgs_search,query,max_results))
    except Exception as e:logger.warning("Primary web search failed: error=%s",type(e).__name__)
    for backend in ("google","bing"):
        try:
            results=_normalise(await asyncio.to_thread(_ddgs_search,query,max_results,backend))
            if results:return results
        except Exception as e:logger.warning("Fallback web search failed: backend=%s error=%s",backend,type(e).__name__)
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
    item["relevance_reason"]=("Exact phrase" if exact else f"{len(overlap)}/{len(qt)} key terms match")
    item["relevant"]=bool(exact or len(overlap)>=required)
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

    news_batches=await asyncio.gather(*[_google_news(q,max_results) for q in queries],return_exceptions=True)
    news_results=[]
    for batch in news_batches:
        if isinstance(batch,list):news_results.extend(batch)

    ddgs_batches=await asyncio.gather(*[_ddgs(q,min(max_results,6)) for q in queries[:2]],return_exceptions=True)
    ddgs_results=[]
    for batch in ddgs_batches:
        if isinstance(batch,list):ddgs_results.extend(batch)

    results=_normalise(news_results+ddgs_results)
    if not results:raise SearchError("No live web evidence was retrieved from available search providers")
    for item in results:_relevance(query,item)

    relevant=[x for x in results if x.get("relevant")]
    pool=relevant or sorted(results,key=lambda x:(x["source_quality"],x.get("relevance_score",0),len(x["content"])),reverse=True)[:max(4,min(max_results,8))]
    ranked=sorted(pool,key=lambda x:(x.get("relevance_score",0),x["source_quality"],len(x["content"])),reverse=True)
    seen_urls=set();seen_domains=set();out=[]
    for item in ranked:
        if item["url"] in seen_urls:continue
        domain=item["publisher"]
        if domain in seen_domains and item["source_quality"]<90:continue
        out.append(item);seen_urls.add(item["url"]);seen_domains.add(domain)
        if len(out)>=max_results:break
    counts=Counter(x["publisher"] for x in out)
    for i,x in enumerate(out,1):
        x["evidence_id"]=f"E{i:02d}";x["corroboration_count"]=counts[x["publisher"]]
    return out