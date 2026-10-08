import logging
from fastapi import APIRouter,HTTPException,UploadFile,File,Form,Request,Response
from ..models.factcheck import FactCheckRequest,URLFactCheckRequest
from ..services.fact_checker import run_fact_check,run_url_fact_check
from ..services.media import extract_media,MediaCapabilityError,MediaRateLimitError,MediaCallBudget
from ..services.groq import GroqPayloadTooLargeError,GroqProviderError,GroqRateLimitError
from ..services.search import SearchError
from ..config import get_settings
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
    except GroqPayloadTooLargeError as e:
        raise HTTPException(413,str(e)) from e
    except GroqRateLimitError as e:
        raise HTTPException(429,str(e),headers={"Retry-After":str(max(1,int(e.retry_after)))}) from e
    except GroqProviderError as e:
        raise HTTPException(503,str(e)) from e
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
    if not text.strip(): raise HTTPException(400,"Input is required. Add the claim, question, statement, or text you want researched.")
    if len(files)>5: raise HTTPException(400,"You can attach up to 5 files per fact check.")
    try:
        budget=MediaCallBudget(get_settings().media_vision_calls_per_request)
        contexts=[]
        media_errors=[]
        # Attachments are optional context: one unavailable attachment must not
        # prevent the user's primary text from being researched.
        for file in files:
            try:
                contexts.append(await extract_media(file,budget))
            except MediaRateLimitError as e:
                media_errors.append(f"{file.filename or 'Attachment'}: {e}")
                logger.warning("Optional media analysis rate-limited: filename=%s",file.filename)
            except MediaCapabilityError as e:
                media_errors.append(f"{file.filename or 'Attachment'}: {e}")
                logger.warning("Optional media analysis unavailable: filename=%s",file.filename)
            except ValueError as e:
                media_errors.append(f"{file.filename or 'Attachment'}: {e}")
                logger.warning("Optional media rejected: filename=%s",file.filename)
        seed=text.strip() or "Analyze the attached media and identify the claims that require verification."
        validated=FactCheckRequest(text=seed,content_mode=content_mode,detail=detail,audience=audience,source_preference=source_preference,region=region,language=language)
        r=await run_fact_check(validated.text,prefs(validated),contexts,media_only=not text.strip())
        if media_errors:
            r.uncertainties.extend(media_errors)
    except ValueError as e: raise HTTPException(400,str(e)) from e
    except MediaRateLimitError as e:
        headers={"Retry-After":str(max(1,int(e.retry_after or 5)))}
        raise HTTPException(429,str(e),headers=headers) from e
    except MediaCapabilityError as e:
        raise HTTPException(503,str(e)) from e
    except GroqPayloadTooLargeError as e:
        raise HTTPException(413,str(e)) from e
    except GroqRateLimitError as e:
        raise HTTPException(429,str(e),headers={"Retry-After":str(max(1,int(e.retry_after)))}) from e
    except GroqProviderError as e:
        raise HTTPException(503,str(e)) from e
    except SearchError as e:
        logger.exception("Live evidence retrieval failed",extra={"file_count":len(files),"input_length":len(text)})
        raise HTTPException(502,"Live web evidence could not be retrieved right now. Please try again.") from e
    except Exception as e:
        logger.exception("Media fact-check failed",extra={"file_count":len(files),"input_length":len(text)})
        raise HTTPException(502,"We couldn't complete this investigation right now. Please try again.") from e
    i=await _save(r,"claim",r.claim[:120],owner(request,response));return {"id":i,**r.model_dump(mode="json")} 

@router.post("/fact-check/url")
async def url_fact_check(req:URLFactCheckRequest, request: Request, response: Response):
    try:r=await run_url_fact_check(str(req.url),prefs(req))
    except ValueError as e: raise HTTPException(400,str(e))
    except GroqPayloadTooLargeError as e: raise HTTPException(413,str(e)) from e
    except GroqProviderError as e: raise HTTPException(503,str(e)) from e
    except Exception as e:
        logger.exception("Article fact-check failed",extra={"url_host":req.url.host})
        raise HTTPException(502,"We couldn't complete this article fact check right now. Please try again.") from e
    try:i=store.add("article",r.article_title or str(r.article_url),r.overall_verdict.value,r.overall_confidence,r.model_dump(mode="json"),r.last_checked,owner(request,response))
    except Exception as e:
        logger.exception("Article fact-check history save failed")
        raise HTTPException(503,"The article fact check completed, but the result could not be saved. Please try again.") from e
    return {"id":i,**r.model_dump(mode="json")}
