import logging
from fastapi import APIRouter,HTTPException
from ..models.factcheck import FactCheckRequest,URLFactCheckRequest
from ..services.fact_checker import run_fact_check,run_url_fact_check
from ..services.history import HistoryStore

logger=logging.getLogger(__name__)
router=APIRouter();store=HistoryStore()

def prefs(r):
    return {"content_mode":r.content_mode,"detail":r.detail,"audience":r.audience,"source_preference":r.source_preference,"region":r.region,"language":r.language}

@router.post("/fact-check")
async def fact_check(req:FactCheckRequest):
    try:
        r=await run_fact_check(req.text,prefs(req))
    except Exception as e:
        logger.exception("Fact-check failed",extra={"input_length":len(req.text)})
        raise HTTPException(502,"We couldn't complete this fact check right now. Please try again.") from e
    try:
        i=store.add("claim",r.claim[:120],r.verdict.value,r.confidence,r.model_dump(mode="json"),r.last_checked)
    except Exception as e:
        logger.exception("Fact-check history save failed")
        raise HTTPException(503,"The fact check completed, but the result could not be saved. Please try again.") from e
    return {"id":i,**r.model_dump(mode="json")}

@router.post("/fact-check/url")
async def url_fact_check(req:URLFactCheckRequest):
    try:
        r=await run_url_fact_check(str(req.url),prefs(req))
    except ValueError as e:
        raise HTTPException(400,str(e))
    except Exception as e:
        logger.exception("Article fact-check failed",extra={"url_host":req.url.host})
        raise HTTPException(502,"We couldn't complete this article fact check right now. Please try again.") from e
    try:
        i=store.add("article",r.article_title or str(r.article_url),r.overall_verdict.value,r.overall_confidence,r.model_dump(mode="json"),r.last_checked)
    except Exception as e:
        logger.exception("Article fact-check history save failed")
        raise HTTPException(503,"The article fact check completed, but the result could not be saved. Please try again.") from e
    return {"id":i,**r.model_dump(mode="json")}
