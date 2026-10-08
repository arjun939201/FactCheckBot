from urllib.parse import urlparse

_OFFICIAL_HOSTS = {
    "india.gov.in", "pib.gov.in", "pmo.gov.in", "presidentofindia.gov.in",
    "sansad.in", "loksabha.nic.in", "rajyasabha.nic.in", "eci.gov.in",
    "sci.gov.in", "main.sci.gov.in", "indiacode.nic.in", "egazette.nic.in",
}
_ACADEMIC_HOSTS = {
    "who.int", "un.org", "imf.org", "worldbank.org", "nature.com",
    "science.org", "pubmed.ncbi.nlm.nih.gov", "arxiv.org",
}
_NEWS_NAMES = {
    "reuters", "associated press", "ap news", "bbc", "the guardian",
    "new york times", "washington post", "the hindu", "indian express",
    "times of india", "new indian express",
}

def normalize_url(url:str)->str|None:
    try:
        p=urlparse(url)
        return url if p.scheme in {"http","https"} and p.netloc else None
    except Exception:
        return None

def _host_matches(host:str, domains:set[str])->bool:
    return any(host == domain or host.endswith("." + domain) for domain in domains)

def source_type_for(title:str,publisher:str,url:str)->str:
    try:
        host=(urlparse(url).hostname or "").lower().rstrip(".")
    except Exception:
        host=""
    publisher_text=str(publisher or "").lower().strip()
    title_text=str(title or "").lower()
    # Classify official sources from the actual source host, never because a
    # headline merely contains words such as "government" or "official".
    # Search-aggregator URLs cannot be upgraded to official based on headline text.
    if host not in {"news.google.com","google.com"} and (
        host.endswith(".gov.in") or _host_matches(host,_OFFICIAL_HOSTS)
    ):
        return "Government"
    if host not in {"news.google.com","google.com"} and _host_matches(host,_ACADEMIC_HOSTS):
        return "Academic/Scientific"
    if any(name in publisher_text for name in _NEWS_NAMES) or any(
        name in host for name in ("reuters.com","apnews.com","bbc.com","theguardian.com","nytimes.com","washingtonpost.com")
    ):
        return "Major news organization"
    if "fact" in publisher_text and "check" in publisher_text:
        return "Fact-checking organization"
    if host.endswith(".gov") and host not in {"news.google.com","google.com"}:
        return "Government"
    if any(name in title_text for name in ("research paper","journal article")):
        return "Academic/Scientific"
    return "Other"

def allowed_source_url(url:str,known_urls:set[str])->bool:
    return bool(normalize_url(url) and url in known_urls)
