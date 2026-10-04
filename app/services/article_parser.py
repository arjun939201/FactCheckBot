import ipaddress,socket,urllib.robotparser
from urllib.parse import urlparse
import httpx
from bs4 import BeautifulSoup
from ..config import get_settings
class ArticleFetchError(Exception):pass
def _safe_host(host:str)->bool:
    try:
        for item in socket.getaddrinfo(host,None):
            ip=ipaddress.ip_address(item[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:return False
        return True
    except Exception:return False
async def _robots_allowed(url:str,ua:str)->bool:
    p=urlparse(url); robots=f"{p.scheme}://{p.netloc}/robots.txt"
    try:
        async with httpx.AsyncClient(timeout=8,follow_redirects=True,headers={"User-Agent":ua}) as c:r=await c.get(robots)
        if r.status_code==404:return True
        if r.status_code>=400:return False
        rp=urllib.robotparser.RobotFileParser(); rp.parse(r.text.splitlines()); return rp.can_fetch(ua,url)
    except Exception:return False
async def fetch_article(url:str)->tuple[str,str]:
    p=urlparse(url)
    if p.scheme not in {"http","https"} or not p.hostname or not _safe_host(p.hostname):raise ArticleFetchError("URL is not allowed")
    s=get_settings(); ua="FactCheck/1.0; +https://github.com/arjun939201/FactCheckbot"
    if not await _robots_allowed(url,ua):raise ArticleFetchError("The site does not allow automated retrieval of this page")
    try:
        async with httpx.AsyncClient(timeout=s.request_timeout,follow_redirects=True,headers={"User-Agent":ua}) as c:
            r=await c.get(url); r.raise_for_status(); final=urlparse(str(r.url))
            if not final.hostname or not _safe_host(final.hostname):raise ArticleFetchError("Redirect target is not allowed")
            if "text/html" not in r.headers.get("content-type",""):raise ArticleFetchError("URL is not an HTML article")
            soup=BeautifulSoup(r.text,"html.parser")
            for tag in soup(["script","style","noscript","nav","footer","form","aside"]):tag.decompose()
            t=soup.find("meta",property="og:title") or soup.title
            title=t.get("content","") if t and t.name=="meta" else (t.get_text(" ",strip=True) if t else "")
            article=soup.find("article") or soup.body; text=" ".join(article.stripped_strings) if article else ""
            return title[:500],text[:s.max_article_chars]
    except ArticleFetchError:raise
    except Exception as e:raise ArticleFetchError("Unable to retrieve article") from e
