import httpx
from urllib.parse import urlparse
from ..config import get_settings
from .source_validator import source_type_for
class SearchError(Exception):pass
async def search_web(query:str,max_results:int=6)->list[dict]:
    s=get_settings()
    if not s.search_api_key:return []
    endpoint=s.search_api_url or "https://api.tavily.com/search"
    try:
        async with httpx.AsyncClient(timeout=s.request_timeout) as c:
            r=await c.post(endpoint,json={"api_key":s.search_api_key,"query":query,"search_depth":"basic","max_results":max_results,"include_answer":False})
            r.raise_for_status(); data=r.json()
    except Exception as e:raise SearchError("Evidence provider request failed") from e
    out=[]
    for x in data.get("results",[]):
        u=x.get("url","")
        if urlparse(u).scheme not in {"http","https"}:continue
        out.append({"title":x.get("title","")[:300],"url":u,"content":x.get("content","")[:5000],
                    "publisher":urlparse(u).netloc,"source_type":source_type_for(x.get("title",""),urlparse(u).netloc,u)})
    return out
