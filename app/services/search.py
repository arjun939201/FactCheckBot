import asyncio
import logging
import re
from urllib.parse import quote_plus,urlparse
from xml.etree import ElementTree as ET

import httpx
from ddgs import DDGS

from .source_validator import source_type_for

logger=logging.getLogger(__name__)

class SearchError(Exception):pass

def _normalise(results):
    out=[];seen=set()
    for x in results:
        u=x.get("href") or x.get("url") or ""
        if urlparse(u).scheme not in {"http","https"} or u in seen:continue
        seen.add(u)
        out.append({"title":str(x.get("title",""))[:300],"url":u,"content":str(x.get("body") or x.get("description") or "")[:5000],"publisher":urlparse(u).netloc,"source_type":source_type_for(str(x.get("title","")),urlparse(u).netloc,u)})
    return out

def _ddgs_search(query,max_results,backend=None):
    kwargs={"max_results":max_results}
    if backend:kwargs["backend"]=backend
    return list(DDGS().text(query,**kwargs))

async def _ddgs(query,max_results):
    try:
        return _normalise(await asyncio.to_thread(_ddgs_search,query,max_results))
    except Exception as e:
        logger.warning("Primary web search failed: error=%s",type(e).__name__)
    for backend in ("google","bing"):
        try:
            results=_normalise(await asyncio.to_thread(_ddgs_search,query,max_results,backend))
            if results:return results
        except Exception as e:
            logger.warning("Fallback web search failed: backend=%s error=%s",backend,type(e).__name__)
    return []

async def _google_news(query,max_results):
    url="https://news.google.com/rss/search?q="+quote_plus(query)+"&hl=en-IN&gl=IN&ceid=IN:en"
    try:
        async with httpx.AsyncClient(timeout=8,follow_redirects=True,headers={"User-Agent":"FactCheck/1.0"}) as client:
            r=await client.get(url);r.raise_for_status()
        root=ET.fromstring(r.text)
    except Exception as e:
        logger.warning("Google News RSS failed: error=%s",type(e).__name__)
        return []
    results=[]
    for item in root.findall(".//item")[:max_results]:
        title=item.findtext("title") or ""
        link=item.findtext("link") or ""
        description=re.sub("<[^>]+>"," ",item.findtext("description") or "").strip()
        results.append({"title":title,"url":link,"body":description})
    return _normalise(results)

def _queries(query):
    q=" ".join(query.split())
    parts=[p.strip() for p in re.split(r"(?<=[.!?])\s+",q) if p.strip()]
    queries=[q]+parts[:2]
    if len(q)>180:queries.append(q[:180])
    return list(dict.fromkeys(queries))[:3]

async def search_web(query:str,max_results:int=6)->list[dict]:
    queries=_queries(query)
    batches=await asyncio.gather(*[_ddgs(q,max_results) for q in queries],*[_google_news(q,max_results) for q in queries])
    results=_normalise([item for batch in batches for item in batch])
    if not results:raise SearchError("No live web evidence was retrieved")
    return results[:max_results*2]
