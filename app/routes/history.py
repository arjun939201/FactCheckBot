from fastapi import APIRouter,HTTPException
from ..services.history import HistoryStore
router=APIRouter();store=HistoryStore()
@router.get("/history")
def history(q:str=""):return {"items":store.list(q)}
@router.get("/history/{id}")
def item(id:int):
    x=store.get(id)
    if not x:raise HTTPException(404,"History item not found")
    return x
@router.delete("/history/{id}")
def delete(id:int):
    if not store.delete(id):raise HTTPException(404,"History item not found")
    return {"deleted":True}
@router.delete("/history")
def clear():store.clear();return {"cleared":True}
