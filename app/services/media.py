import asyncio,base64,io,json,os,subprocess,tempfile,logging,time
from dataclasses import dataclass
import httpx
from PIL import Image
from fastapi import UploadFile
from ..config import get_settings
from .groq import ai_request_started, ai_request_succeeded, ai_request_failed, ai_request_finished, record_ai_rate_limit

logger=logging.getLogger(__name__)

MAX_IMAGE=10*1024*1024
MAX_AUDIO=25*1024*1024
MAX_VIDEO=25*1024*1024
IMAGES={"image/jpeg","image/png","image/webp","image/gif"}
AUDIOS={"audio/mpeg","audio/mp3","audio/wav","audio/x-wav","audio/mp4","audio/m4a","audio/ogg","audio/webm"}
VIDEOS={"video/mp4","video/webm","video/quicktime","video/mpeg"}
VISION_CACHE_TTL=300
_vision_cache:tuple[float,list[str]]=(0.0,[])

class MediaCapabilityError(RuntimeError):
    """Raised when the configured provider cannot process a media type."""

class MediaRateLimitError(RuntimeError):
    """Raised when the upstream multimodal provider is throttling requests."""
    def __init__(self,message:str,retry_after:float|None=None):
        super().__init__(message)
        self.retry_after=retry_after

@dataclass
class MediaCallBudget:
    remaining:int|None=None

    def consume(self)->None:
        if self.remaining is not None and self.remaining <= 0:
            raise MediaCapabilityError("This investigation reached its media-analysis limit. Reduce the number of attachments or frames and try again.")
        if self.remaining is not None:
            self.remaining -= 1

@dataclass
class MediaContext:
    filename:str
    media_type:str
    size_bytes:int
    kind:str
    extracted_text:str=""
    visual_summary:str=""

async def _read(f,limit):
    data=await f.read(limit+1)
    if not data: raise ValueError("Attachment is empty.")
    if len(data)>limit: raise ValueError("Attachment exceeds the allowed size.")
    return data

def _data_url(data,media_type):
    return "data:"+media_type+";base64,"+base64.b64encode(data).decode()

def _contains_image_capability(value):
    if isinstance(value,str):
        v=value.lower().replace("_","-")
        return v in {"image","images","vision","multimodal","image-input","image-inputs"} or "image" in v or "vision" in v
    if isinstance(value,list): return any(_contains_image_capability(x) for x in value)
    if isinstance(value,dict): return any(_contains_image_capability(x) for x in value.values())
    return False

def _vision_candidates(models):
    """Select models from Groq metadata, with legacy ID fallback.

    Groq has changed model IDs and metadata over time. Capability metadata is
    preferred; name matching is retained only for older responses that expose
    no modality information.
    """
    candidates=[]
    legacy=("llama-4-scout","llama-4-maverick","vision","qwen2-vl","qwen-vl","gemma-3")
    for item in models:
        if isinstance(item,str):
            model_id=item; capable=any(p in model_id.lower() for p in legacy)
        elif isinstance(item,dict):
            model_id=str(item.get("id","")).strip()
            capable=_contains_image_capability({k:item.get(k) for k in ("architecture","modalities","input_modalities","supported_modalities","capabilities","features") if k in item})
            if not capable: capable=any(p in model_id.lower() for p in legacy)
        else:
            continue
        if model_id and capable and model_id not in candidates: candidates.append(model_id)
    return candidates

async def _discover_vision_models(force=False):
    global _vision_cache
    now=time.monotonic()
    if not force and now-_vision_cache[0] < VISION_CACHE_TTL:
        return _vision_cache[1]
    s=get_settings()
    try:
        async with httpx.AsyncClient(timeout=s.request_timeout) as c:
            r=await c.get("https://api.groq.com/openai/v1/models",headers={"Authorization":"Bearer "+s.groq_api_key})
            r.raise_for_status()
            payload=r.json()
    except httpx.HTTPError:
        raise
    models=payload.get("data",[]) if isinstance(payload,dict) else []
    candidates=_vision_candidates(models)
    _vision_cache=(now,candidates)
    return candidates

async def _call_vision_model_impl(model,data,media_type,prompt):
    s=get_settings()
    max_retries=s.media_vision_retry_attempts
    base_delay=1.0
    max_delay=s.media_vision_retry_max_delay
    last_429=None
    for attempt in range(max_retries+1):
        async with httpx.AsyncClient(timeout=s.request_timeout) as c:
            r=await c.post("https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization":"Bearer "+s.groq_api_key,"Content-Type":"application/json"},
                json={"model":model,"temperature":0.1,"messages":[{"role":"user","content":[
                    {"type":"text","text":prompt},
                    {"type":"image_url","image_url":{"url":_data_url(data,media_type)}}
                ]}]})
        if r.status_code==429:
            retry_header=r.headers.get("Retry-After")
            try:
                requested_delay=float(retry_header) if retry_header else base_delay*(2**attempt)
            except ValueError:
                requested_delay=base_delay*(2**attempt)
            requested_delay=max(0.0,requested_delay)
            last_429=r
            logger.warning("Groq vision rate limited: model=%s attempt=%s retry_after=%.2fs",model,attempt+1,requested_delay)
            if attempt < max_retries and requested_delay <= max_delay:
                await asyncio.sleep(requested_delay)
                continue
            raise MediaRateLimitError(
                "Groq image analysis is temporarily rate-limited. Please wait and try again.",
                retry_after=requested_delay,
            )
        if r.status_code==404:
            raise httpx.HTTPStatusError("Groq vision model unavailable",request=r.request,response=r)
        r.raise_for_status()
        body=r.json()
        content=body["choices"][0]["message"]["content"].strip()
        fence=chr(96)*3
        if content.startswith(fence): content=content.split("\n",1)[-1].rsplit(fence,1)[0].strip()
        return json.loads(content)
    raise MediaRateLimitError("Groq image analysis is temporarily rate-limited. Please wait and try again.") from last_429


async def _call_vision_model(model,data,media_type,prompt):
    ai_request_started()
    try:
        result = await _call_vision_model_impl(model,data,media_type,prompt)
        ai_request_succeeded()
        return result
    except MediaRateLimitError as e:
        record_ai_rate_limit(e.retry_after or 10.0)
        raise
    except (httpx.HTTPError, MediaCapabilityError):
        ai_request_failed()
        raise
    finally:
        ai_request_finished()

async def _vision(data,media_type,budget:MediaCallBudget|None=None):
    s=get_settings()
    if not s.groq_api_key: raise MediaCapabilityError("Groq API is not configured for image analysis.")
    budget=budget or MediaCallBudget(s.media_vision_calls_per_request)
    budget.consume()
    prompt='''Analyze this uploaded image for a fact-checking system. Return JSON only:
{"visible_text":"readable text","visual_summary":"objective visual description","claims_or_context":["verifiable claims suggested by the image"],"uncertainties":["things not established by the image"]}
Do not infer identity, intent, authenticity, location, date, or events unless directly visible.'''

    configured=[]
    for model in (s.groq_vision_model,s.groq_vision_fallback_model):
        if model and model not in configured: configured.append(model)

    last_error=None
    for model in configured:
        try:
            return await _call_vision_model(model,data,media_type,prompt)
        except httpx.HTTPStatusError as e:
            last_error=e
            if e.response.status_code!=404: raise
            logger.warning("Configured Groq vision model unavailable: %s",model)

    try:
        discovered=await _discover_vision_models(force=True)
    except httpx.HTTPError as e:
        logger.exception("Groq multimodal model discovery failed")
        raise MediaCapabilityError("Groq image analysis is temporarily unavailable because the model catalog could not be checked.") from e

    logger.info("Discovered Groq multimodal candidates: %s",discovered)
    for model in discovered:
        if model in configured: continue
        try:
            return await _call_vision_model(model,data,media_type,prompt)
        except httpx.HTTPStatusError as e:
            if e.response.status_code==404:
                logger.warning("Discovered Groq vision candidate unavailable: %s",model)
                continue
            raise

    raise MediaCapabilityError(
        "Image analysis is currently unavailable for this Groq account. "
        "The account has no usable multimodal model. Set GROQ_VISION_MODEL to a currently available vision-capable Groq model."
    ) from last_error

async def _transcribe(data,filename,media_type):
    s=get_settings()
    if not s.groq_api_key: raise RuntimeError("Groq API is not configured")
    async with httpx.AsyncClient(timeout=max(s.request_timeout,60)) as c:
        r=await c.post("https://api.groq.com/openai/v1/audio/transcriptions",
          headers={"Authorization":"Bearer "+s.groq_api_key},
          files={"file":(filename,data,media_type)},
          data={"model":s.groq_transcription_model,"response_format":"text"})
        r.raise_for_status()
        return r.text.strip()

def _ffmpeg(): return __import__("imageio_ffmpeg").get_ffmpeg_exe()

def _frames(data,suffix):
    with tempfile.TemporaryDirectory() as td:
        src=os.path.join(td,"input"+suffix); out=os.path.join(td,"frame-%02d.jpg")
        open(src,"wb").write(data)
        subprocess.run([_ffmpeg(),"-y","-i",src,"-vf","fps=1/5,scale=1280:-2","-frames:v","3",out],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False,timeout=30)
        return [open(os.path.join(td,n),"rb").read() for n in sorted(os.listdir(td)) if n.startswith("frame-") and n.endswith(".jpg")]

def _audio(data,suffix):
    with tempfile.TemporaryDirectory() as td:
        src=os.path.join(td,"input"+suffix); out=os.path.join(td,"audio.mp3")
        open(src,"wb").write(data)
        subprocess.run([_ffmpeg(),"-y","-i",src,"-vn","-ac","1","-ar","16000","-b:a","64k",out],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False,timeout=45)
        return (open(out,"rb").read(),"audio/mpeg") if os.path.exists(out) else (None,"audio/mpeg")

async def extract_media(file:UploadFile,budget:MediaCallBudget|None=None)->MediaContext:
    mt=(file.content_type or "").lower(); name=file.filename or "attachment"
    s=get_settings()
    budget=budget or MediaCallBudget(s.media_vision_calls_per_request)
    if mt in IMAGES:
        data=await _read(file,MAX_IMAGE)
        try:
            im=Image.open(io.BytesIO(data)); im.thumbnail((1600,1600))
            buf=io.BytesIO(); im.convert("RGB").save(buf,format="JPEG",quality=85,optimize=True); data=buf.getvalue()
        except Exception as e: raise ValueError("The uploaded image could not be decoded.") from e
        x=await _vision(data,"image/jpeg",budget)
        extra="\n".join(x.get("claims_or_context",[])+x.get("uncertainties",[]))
        return MediaContext(name,mt,len(data),"image",x.get("visible_text",""),x.get("visual_summary","")+("\n"+extra if extra else ""))
    if mt in AUDIOS:
        data=await _read(file,MAX_AUDIO)
        return MediaContext(name,mt,len(data),"audio",await _transcribe(data,name,mt),"Audio transcription supplied for claim analysis.")
    if mt in VIDEOS:
        data=await _read(file,MAX_VIDEO); suffix=os.path.splitext(name)[1] or ".mp4"; desc=[]
        for frame in _frames(data,suffix):
            try:
                x=await _vision(frame,"image/jpeg",budget); desc.append(x.get("visual_summary",""))
                if x.get("visible_text"): desc.append("Visible text: "+x["visible_text"])
            except MediaRateLimitError:
                logger.warning("Video visual analysis rate-limited; continuing with audio transcription")
                break
            except MediaCapabilityError:
                logger.warning("Video visual analysis unavailable; continuing with audio transcription")
                break
            except Exception:
                logger.exception("Video frame analysis failed; continuing with remaining media")
        aud,amt=_audio(data,suffix); transcript=await _transcribe(aud,"video-audio.mp3",amt) if aud else ""
        if not transcript and not desc: raise MediaCapabilityError("Video analysis produced no usable audio or visual content.")
        return MediaContext(name,mt,len(data),"video",transcript,"\n".join(x for x in desc if x))
    raise ValueError("Unsupported attachment. Use JPG, PNG, WEBP, GIF, MP3, WAV, M4A, OGG, WEBM, MP4, MOV, or MPEG.")
