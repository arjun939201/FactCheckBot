import logging
from fastapi import APIRouter,HTTPException,UploadFile,File,Form,Request,Response
from ..models.factcheck import FactCheckRequest,URLFactCheckRequest
from ..services.fact_checker import run_fact_check,run_url_fact_check
from ..services.media import extract_media
from .history import owner, store

logger=logging.getLogger(__name__)
router=APIRouter()

def prefs(r):
    return {"content_mode":r.content_mode,"detail":r.detail,"audience":r.audience,"source_preference":r.source_preference,"region":r.region,"language":r.language}

async def _save(r,kind,title,owner_key):
    try:return store.add(kind,title,r.verdict.value,r.confidence,r.model_dump(mode="json"),r.last_checked,owner_key)
    except Exception as e:
        logger.exception("Fact-check history save failed")
        raise HTTPException(503,"The fact check completed, but the result could not be saved. Please try again.") from e

@router.post("/fact-check")
async def fact_check(req:FactCheckRequest, request: Request, response: Response):
    try:r=await run_fact_check(req.text,prefs(req))
    except Exception as e:
        logger.exception("Fact-check failed",extra={"input_length":len(req.text)})
        raise HTTPException(502,"We couldn't complete this fact check right now. Please try again.") from e
    i=await _save(r,"claim",r.claim[:120],owner(request,response));return {"id":i,**r.model_dump(mode="json")} 

@router.post("/fact-check/media")
async def media_fact_check(
    request: Request, response: Response,
    text:str=Form(""),content_mode:str=Form("auto"),detail:str=Form("standard"),
    audience:str=Form("general"),source_preference:str=Form("any"),region:str=Form("global"),
    language:str=Form("English"),files:list[UploadFile]=File(default=[]),
):
    if not text.strip() and not files: raise HTTPException(400,"Add claim text or at least one attachment.")
    if len(files)>5: raise HTTPException(400,"You can attach up to 5 files per fact check.")
    try:
        contexts=[await extract_media(file) for file in files]
        seed=text.strip() or "Analyze the attached media and identify the claims that require verification."
        validated=FactCheckRequest(text=seed,content_mode=content_mode,detail=detail,audience=audience,source_preference=source_preference,region=region,language=language)
        r=await run_fact_check(validated.text,prefs(validated),contexts)
    except ValueError as e: raise HTTPException(400,str(e)) from e
    except Exception as e:
        logger.exception("Media fact-check failed",extra={"file_count":len(files),"input_length":len(text)})
        raise HTTPException(502,"We couldn't analyze the attachment(s) right now. Please try again.") from e
    i=await _save(r,"claim",r.claim[:120],owner(request,response));return {"id":i,**r.model_dump(mode="json")} 

@router.post("/fact-check/url")
async def url_fact_check(req:URLFactCheckRequest, request: Request, response: Response):
    try:r=await run_url_fact_check(str(req.url),prefs(req))
    except ValueError as e: raise HTTPException(400,str(e))
    except Exception as e:
        logger.exception("Article fact-check failed",extra={"url_host":req.url.host})
        raise HTTPException(502,"We couldn't complete this article fact check right now. Please try again.") from e
    try:i=store.add("article",r.article_title or str(r.article_url),r.overall_verdict.value,r.overall_confidence,r.model_dump(mode="json"),r.last_checked,owner(request,response))
    except Exception as e:
        logger.exception("Article fact-check history save failed")
        raise HTTPException(503,"The article fact check completed, but the result could not be saved. Please try again.") from e
    return {"id":i,**r.model_dump(mode="json")}
