import asyncio
from urllib.parse import urlparse
from ddgs import DDGS
from .source_validator import source_type_for

class SearchError(Exception):pass

def _search_sync(query:str,max_results:int):
    return list(DDGS().text(query,max_results=max_results))

async def search_web(query:str,max_results:int=6)->list[dict]:
    try:
        results=await asyncio.to_thread(_search_sync,query,max_results)
    except Exception as e:
        raise SearchError("Free web search failed") from e
    out=[]
    for x in results:
        u=x.get("href") or x.get("url") or ""
        if urlparse(u).scheme not in {"http","https"}:continue
        out.append({
            "title":x.get("title","")[:300],
            "url":u,
            "content":x.get("body","")[:5000],
            "publisher":urlparse(u).netloc,
            "source_type":source_type_for(x.get("title",""),urlparse(u).netloc,u)
        })
    return out
