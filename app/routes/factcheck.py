from fastapi import APIRouter,HTTPException
from ..models.factcheck import FactCheckRequest,URLFactCheckRequest
from ..services.fact_checker import run_fact_check,run_url_fact_check
from ..services.history import HistoryStore
router=APIRouter();store=HistoryStore()
def prefs(r):return {"detail":r.detail,"audience":r.audience,"source_preference":r.source_preference,"region":r.region,"language":r.language}
@router.post("/fact-check")
async def fact_check(req:FactCheckRequest):
    try:r=await run_fact_check(req.text,prefs(req))
    except Exception as e:raise HTTPException(502,"We couldn't complete this fact check right now. Please try again.") from e
    i=store.add("claim",r.claim[:120],r.verdict.value,r.confidence,r.model_dump(mode="json"),r.last_checked)
    return {"id":i,**r.model_dump(mode="json")}
@router.post("/fact-check/url")
async def url_fact_check(req:URLFactCheckRequest):
    try:r=await run_url_fact_check(str(req.url),prefs(req))
    except ValueError as e:raise HTTPException(400,str(e))
    except Exception as e:raise HTTPException(502,"We couldn't complete this article fact check right now. Please try again.") from e
    i=store.add("article",r.article_title or str(r.article_url),r.overall_verdict.value,r.overall_confidence,r.model_dump(mode="json"),r.last_checked)
    return {"id":i,**r.model_dump(mode="json")}
