import base64,io,json,os,subprocess,tempfile
from dataclasses import dataclass
import httpx
from PIL import Image
from fastapi import UploadFile
from ..config import get_settings

MAX_IMAGE=10*1024*1024
MAX_AUDIO=25*1024*1024
MAX_VIDEO=25*1024*1024
IMAGES={"image/jpeg","image/png","image/webp","image/gif"}
AUDIOS={"audio/mpeg","audio/mp3","audio/wav","audio/x-wav","audio/mp4","audio/m4a","audio/ogg","audio/webm"}
VIDEOS={"video/mp4","video/webm","video/quicktime","video/mpeg"}

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

async def _vision(data,media_type):
    s=get_settings()
    if not s.groq_api_key: raise RuntimeError("Groq API is not configured")
    prompt='''Analyze this uploaded image for a fact-checking system. Return JSON only:
{"visible_text":"readable text","visual_summary":"objective visual description","claims_or_context":["verifiable claims suggested by the image"],"uncertainties":["things not established by the image"]}
Do not infer identity, intent, authenticity, location, date, or events unless directly visible.'''
    models=[s.groq_vision_model]
    if s.groq_vision_fallback_model and s.groq_vision_fallback_model not in models: models.append(s.groq_vision_fallback_model)
    last_error=None
    for model in models:
        try:
            async with httpx.AsyncClient(timeout=s.request_timeout) as c:
                r=await c.post("https://api.groq.com/openai/v1/chat/completions",headers={"Authorization":"Bearer "+s.groq_api_key,"Content-Type":"application/json"},json={"model":model,"temperature":0.1,"messages":[{"role":"user","content":[{"type":"text","text":prompt},{"type":"image_url","image_url":{"url":_data_url(data,media_type)}}]}]})
                if r.status_code==404 and model != models[-1]: continue
                r.raise_for_status()
                content=r.json()["choices"][0]["message"]["content"].strip()
            fence=chr(96)*3
            if content.startswith(fence): content=content.split("\n",1)[-1].rsplit(fence,1)[0].strip()
            return json.loads(content)
        except httpx.HTTPStatusError as e:
            last_error=e
            if e.response.status_code!=404: raise
    raise RuntimeError("No configured Groq vision model is available. Check GROQ_VISION_MODEL and GROQ_VISION_FALLBACK_MODEL.") from last_error

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

def _ffmpeg():
    return __import__("imageio_ffmpeg").get_ffmpeg_exe()

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

async def extract_media(file:UploadFile)->MediaContext:
    mt=(file.content_type or "").lower(); name=file.filename or "attachment"
    if mt in IMAGES:
        data=await _read(file,MAX_IMAGE)
        try:
            im=Image.open(io.BytesIO(data)); im.thumbnail((1600,1600))
            buf=io.BytesIO(); im.convert("RGB").save(buf,format="JPEG",quality=85,optimize=True)
            data=buf.getvalue()
        except Exception as e: raise ValueError("The uploaded image could not be decoded.") from e
        x=await _vision(data,"image/jpeg")
        extra="\n".join(x.get("claims_or_context",[])+x.get("uncertainties",[]))
        return MediaContext(name,mt,len(data),"image",x.get("visible_text",""),x.get("visual_summary","")+(("\n"+extra) if extra else ""))
    if mt in AUDIOS:
        data=await _read(file,MAX_AUDIO)
        return MediaContext(name,mt,len(data),"audio",await _transcribe(data,name,mt),"Audio transcription supplied for claim analysis.")
    if mt in VIDEOS:
        data=await _read(file,MAX_VIDEO); suffix=os.path.splitext(name)[1] or ".mp4"
        desc=[]
        for frame in _frames(data,suffix):
            try:
                x=await _vision(frame,"image/jpeg")
                desc.append(x.get("visual_summary",""))
                if x.get("visible_text"): desc.append("Visible text: "+x["visible_text"])
            except Exception: pass
        aud,amt=_audio(data,suffix)
        transcript=await _transcribe(aud,"video-audio.mp3",amt) if aud else ""
        return MediaContext(name,mt,len(data),"video",transcript,"\n".join(x for x in desc if x))
    raise ValueError("Unsupported attachment. Use JPG, PNG, WEBP, GIF, MP3, WAV, M4A, OGG, WEBM, MP4, MOV, or MPEG.")
