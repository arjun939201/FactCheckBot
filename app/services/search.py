import asyncio
import logging
import re
from urllib.parse import quote_plus,urlparse
from xml.etree import ElementTree as ET
from collections import Counter
import httpx
from ddgs import DDGS
from .source_validator import source_type_for

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
        u=x.get("href") or x.get("url") or ""
        if urlparse(u).scheme not in {"http","https"} or u in seen:continue
        seen.add(u);title=str(x.get("title",""))[:300];publisher=urlparse(u).netloc
        st=source_type_for(title,publisher,u);quality,tier=_quality(st,publisher,u)
        out.append({"title":title,"url":u,"content":str(x.get("body") or x.get("description") or "")[:5000],"publisher":publisher,"source_type":st,"source_quality":quality,"source_tier":tier})
    return out

def _ddgs_search(query,max_results,backend=None):
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
        async with httpx.AsyncClient(timeout=8,follow_redirects=True,headers={"User-Agent":"FactCheck/1.0"}) as client:
            r=await client.get(url);r.raise_for_status()
        root=ET.fromstring(r.text)
    except Exception as e:logger.warning("Google News RSS failed: error=%s",type(e).__name__);return []
    results=[]
    for item in root.findall(".//item")[:max_results]:
        results.append({"title":item.findtext("title") or "","url":item.findtext("link") or "","body":re.sub("<[^>]+>"," ",item.findtext("description") or "").strip()})
    return _normalise(results)

def _queries(query):
    q=" ".join(query.split());parts=[p.strip() for p in re.split(r"(?<=[.!?])\s+",q) if p.strip()]
    return list(dict.fromkeys([q]+parts[:2]+([q[:180]] if len(q)>180 else [])))[:4]

async def search_web(query:str,max_results:int=6)->list[dict]:
    queries=_queries(query)
    batches=await asyncio.gather(*[_ddgs(q,max_results) for q in queries],*[_google_news(q,max_results) for q in queries])
    results=_normalise([item for batch in batches for item in batch])
    if not results:raise SearchError("No live web evidence was retrieved")
    ranked=sorted(results,key=lambda x:(x["source_quality"],len(x["content"])),reverse=True)
    seen_domains=set();out=[]
    for item in ranked:
        domain=item["publisher"]
        if domain not in seen_domains or len(out)<max_results:
            out.append(item);seen_domains.add(domain)
        if len(out)>=max_results*2:break
    counts=Counter(x["publisher"] for x in out)
    for i,x in enumerate(out,1):
        x["evidence_id"]=f"E{i:02d}";x["corroboration_count"]=counts[x["publisher"]]
    return out[:max_results*2]
