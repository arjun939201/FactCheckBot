from urllib.parse import urlparse
def normalize_url(url:str)->str|None:
    try:
        p=urlparse(url); return url if p.scheme in {"http","https"} and p.netloc else None
    except Exception:return None
def source_type_for(title:str,publisher:str,url:str)->str:
    s=f"{title} {publisher} {url}".lower()
    if ".gov" in s or "government" in s:return "Government"
    if any(x in s for x in ["who.int","un.org","imf.org","worldbank.org","official"]):return "Official organization"
    if any(x in s for x in ["nature.com","science.org","pubmed","arxiv.org","academic"]):return "Academic/Scientific"
    if any(x in s for x in ["reuters","apnews","bbc","theguardian","nytimes","washingtonpost"]):return "Major news organization"
    if "fact" in s and "check" in s:return "Fact-checking organization"
    return "Other"
def allowed_source_url(url:str,known_urls:set[str])->bool:return bool(normalize_url(url) and url in known_urls)
