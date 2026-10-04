from fastapi import APIRouter, HTTPException, Request, Response
from ..services.history import HistoryStore, new_owner_key

router = APIRouter()
store = HistoryStore()
COOKIE = "factcheck_session"


def owner(request: Request, response: Response) -> str:
    value = request.cookies.get(COOKIE)
    if value:
        return value
    value = new_owner_key()
    response.set_cookie(COOKIE, value, httponly=True, secure=request.url.scheme == "https", samesite="strict", max_age=60 * 60 * 24 * 365)
    return value


def existing_owner(request: Request) -> str:
    value = request.cookies.get(COOKIE)
    if not value:
        raise HTTPException(401, "No history session exists yet.")
    return value


@router.get("/history")
def history(request: Request, response: Response, q: str = ""):
    return {"items": store.list(owner(request, response), q[:100])}


@router.get("/history/{id}")
def item(id: int, request: Request):
    x = store.get(id, existing_owner(request))
    if not x:
        raise HTTPException(404, "History item not found")
    return x


@router.delete("/history/{id}")
def delete(id: int, request: Request):
    if not store.delete(id, existing_owner(request)):
        raise HTTPException(404, "History item not found")
    return {"deleted": True}


@router.delete("/history")
def clear(request: Request):
    store.clear(existing_owner(request))
    return {"cleared": True}


@router.get("/share/{id}")
def shared_item(id: int):
    x = store.get(id)
    if not x:
        raise HTTPException(404, "Shared result not found")
    return x
