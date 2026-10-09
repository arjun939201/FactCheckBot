import ipaddress
import socket
import urllib.robotparser
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from ..config import get_settings


class ArticleFetchError(Exception):
    pass


REDIRECT_STATUSES = {301, 302, 303, 307, 308}
MAX_REDIRECTS = 5
MAX_ROBOTS_BYTES = 512 * 1024


def _safe_host(host: str) -> bool:
    """Reject hostnames resolving to any non-public address."""
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        if not infos:
            return False
        for item in infos:
            ip = ipaddress.ip_address(item[4][0])
            if not ip.is_global:
                return False
        return True
    except Exception:
        return False


def _public_http_url(url: str) -> bool:
    try:
        p = urlparse(url)
        if p.scheme not in {"http", "https"} or not p.hostname:
            return False
        if p.username is not None or p.password is not None:
            return False
        port = p.port
        if p.scheme == "http" and port not in (None, 80):
            return False
        if p.scheme == "https" and port not in (None, 443):
            return False
        return _safe_host(p.hostname)
    except (TypeError, ValueError):
        return False


async def _request_limited(url: str, ua: str, timeout: float, max_bytes: int):
    """Fetch one public URL with manually validated redirects and a streaming size cap."""
    current = url
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
        headers={"User-Agent": ua},
    ) as client:
        for hop in range(MAX_REDIRECTS + 1):
            # Validate every destination before making a request. Automatic
            # redirect following is disabled so a redirect cannot reach a
            # private/internal target before validation.
            if not _public_http_url(current):
                raise ArticleFetchError("URL or redirect target is not allowed")
            async with client.stream("GET", current) as response:
                if response.status_code in REDIRECT_STATUSES:
                    location = response.headers.get("location")
                    if not location or hop >= MAX_REDIRECTS:
                        raise ArticleFetchError("Article redirect limit reached")
                    current = urljoin(current, location)
                    continue

                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > max_bytes:
                        raise ArticleFetchError("The page is too large to process safely")
                    body.extend(chunk)
                return response.status_code, response.headers, bytes(body), str(response.url)
    raise ArticleFetchError("Article redirect limit reached")


async def _robots_allowed(url: str, ua: str) -> bool:
    p = urlparse(url)
    robots = f"{p.scheme}://{p.netloc}/robots.txt"
    try:
        status, _headers, body, final_url = await _request_limited(
            robots, ua, timeout=8, max_bytes=MAX_ROBOTS_BYTES
        )
        if not _public_http_url(final_url):
            return False
        if status == 404:
            return True
        if status >= 400:
            return False
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(body.decode("utf-8", errors="replace").splitlines())
        return rp.can_fetch(ua, url)
    except Exception:
        return False


async def fetch_article(url: str) -> tuple[str, str]:
    if not _public_http_url(url):
        raise ArticleFetchError("URL is not allowed")
    settings = get_settings()
    ua = "FactCheck/1.1; +https://github.com/arjun939201/FactCheckBot"
    if not await _robots_allowed(url, ua):
        raise ArticleFetchError("The site does not allow automated retrieval of this page")
    try:
        status, headers, body, final_url = await _request_limited(
            url, ua, timeout=settings.request_timeout,
            max_bytes=settings.max_article_chars * 20,
        )
        if not _public_http_url(final_url):
            raise ArticleFetchError("Redirect target is not allowed")
        if status >= 400:
            raise ArticleFetchError("Unable to retrieve article")
        content_type = headers.get("content-type", "").lower()
        if "text/html" not in content_type:
            raise ArticleFetchError("URL is not an HTML article")
        soup = BeautifulSoup(body, "html.parser")
        for tag in soup(["script", "style", "noscript", "nav", "footer", "form", "aside"]):
            tag.decompose()
        title_tag = soup.find("meta", property="og:title") or soup.title
        title = (
            title_tag.get("content", "")
            if title_tag and title_tag.name == "meta"
            else (title_tag.get_text(" ", strip=True) if title_tag else "")
        )
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
