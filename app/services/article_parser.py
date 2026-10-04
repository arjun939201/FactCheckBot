import ipaddress
import socket
import urllib.robotparser
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from ..config import get_settings


class ArticleFetchError(Exception):
    pass


def _safe_host(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        if not infos:
            return False
        for item in infos:
            ip = ipaddress.ip_address(item[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False
        return True
    except Exception:
        return False


async def _robots_allowed(url: str, ua: str) -> bool:
    p = urlparse(url)
    robots = f"{p.scheme}://{p.netloc}/robots.txt"
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=True, headers={"User-Agent": ua}) as c:
            r = await c.get(robots)
        if r.status_code == 404:
            return True
        if r.status_code >= 400:
            return False
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(r.text.splitlines())
        return rp.can_fetch(ua, url)
    except Exception:
        return False


async def fetch_article(url: str) -> tuple[str, str]:
    p = urlparse(url)
    if p.scheme not in {"http", "https"} or not p.hostname or not _safe_host(p.hostname):
        raise ArticleFetchError("URL is not allowed")
    settings = get_settings()
    ua = "FactCheck/1.1; +https://github.com/arjun939201/FactCheckBot"
    if not await _robots_allowed(url, ua):
        raise ArticleFetchError("The site does not allow automated retrieval of this page")
    try:
        async with httpx.AsyncClient(timeout=settings.request_timeout, follow_redirects=True, max_redirects=5, headers={"User-Agent": ua}) as c:
            r = await c.get(url)
            r.raise_for_status()
            final = urlparse(str(r.url))
            if not final.hostname or not _safe_host(final.hostname):
                raise ArticleFetchError("Redirect target is not allowed")
            content_type = r.headers.get("content-type", "").lower()
            if "text/html" not in content_type:
                raise ArticleFetchError("URL is not an HTML article")
            if len(r.content) > settings.max_article_chars * 20:
                raise ArticleFetchError("The page is too large to process safely")
            soup = BeautifulSoup(r.text, "html.parser")
            for tag in soup(["script", "style", "noscript", "nav", "footer", "form", "aside"]):
                tag.decompose()
            t = soup.find("meta", property="og:title") or soup.title
            title = t.get("content", "") if t and t.name == "meta" else (t.get_text(" ", strip=True) if t else "")
            article = soup.find("article") or soup.body
            text = " ".join(article.stripped_strings) if article else ""
            if not text.strip():
                raise ArticleFetchError("No readable article text was found")
            return title[:500], text[:settings.max_article_chars]
    except ArticleFetchError:
        raise
    except httpx.HTTPError as e:
        raise ArticleFetchError("Unable to retrieve article") from e
    except Exception as e:
        raise ArticleFetchError("Unable to parse article") from e
