from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,Field
from ..services.grok import grok_json
router=APIRouter()
class ChatRequest(BaseModel):
    message:str=Field(min_length=1,max_length=12000); history:list[dict]=[]
@router.post("/chat")
async def chat(req:ChatRequest):
    try:
        history="\n".join(f"{x.get('role','user')}: {x.get('content','')}" for x in req.history[-10:])
        d=await grok_json(f"""Respond naturally to this ordinary chat request. Do not present unsupported claims as verified. Conversation:\n{history}\nUser: {req.message}\nReturn JSON {{\"reply\":\"string\"}}""")
        return {"reply":d.get("reply","")}
    except Exception as e:raise HTTPException(502,"We couldn't complete this chat right now. Please try again.") from e
