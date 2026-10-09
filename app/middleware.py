import time
from collections import defaultdict, deque
from fastapi import Request
from fastapi.responses import JSONResponse
from .config import get_settings

class RequestGuard:
    def __init__(self):
        self.hits=defaultdict(deque)
        self.settings=get_settings()
        self.window=60.0
        self._checks=0
    def allow(self,key,limit):
        now=time.monotonic(); q=self.hits[key]
        while q and now-q[0]>self.window:q.popleft()
        allowed=len(q)<limit
        if allowed:q.append(now)
        self._checks+=1
        # Expired keys from one-off client IPs otherwise accumulate forever.
        if self._checks%128==0 and len(self.hits)>4096:
            cutoff=now-self.window
            for candidate, entries in list(self.hits.items()):
                while entries and entries[0]<cutoff:entries.popleft()
                if not entries:self.hits.pop(candidate,None)
            # Bound memory even during bursts of unique client addresses.
            if len(self.hits)>8192:
                for candidate in list(self.hits)[:len(self.hits)-8192]:
                    self.hits.pop(candidate,None)
        return allowed

_guard=RequestGuard()
EXPENSIVE={"/api/fact-check","/api/fact-check/media","/api/fact-check/url","/api/chat"}

async def request_guard(request:Request,call_next):
    s=get_settings(); path=request.url.path
    if request.method in {"POST","PUT","PATCH","DELETE"} and path in EXPENSIVE:
        ip=request.client.host if request.client else "unknown"
        limit=s.rate_limit_per_minute
        if not _guard.allow(f"{ip}:{path}",limit):
            return JSONResponse({"detail":"Rate limit reached. Please wait a moment and try again."},status_code=429,headers={"Retry-After":"60"})
        length=request.headers.get("content-length")
        if length is not None:
            try:
                declared_length=int(length)
            except (TypeError, ValueError):
                return JSONResponse({"detail":"Invalid Content-Length header."},status_code=400)
            if declared_length < 0:
                return JSONResponse({"detail":"Invalid Content-Length header."},status_code=400)
            if declared_length > s.max_request_bytes:
                return JSONResponse({"detail":"Request is too large."},status_code=413)
    return await call_next(request)
