from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from ..services.groq import groq_json, GroqPayloadTooLargeError, GroqProviderError

router = APIRouter()


class ChatTurn(BaseModel):
    role: str = Field(default="user", pattern="^(user|assistant|system)$")
    content: str = Field(min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=12000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=20)


@router.post("/chat")
async def chat(req: ChatRequest):
    try:
        history = "\n".join(f"{x.role}: {x.content}" for x in req.history[-10:])[-7000:]
        message = req.message[:9000]
        data = await groq_json(f"""Respond naturally to this ordinary chat request. Do not present unsupported claims as verified.
Conversation:
{history}
User: {message}
Return JSON {{"reply":"string"}}""")
        if not isinstance(data, dict) or not isinstance(data.get("reply"), str):
            raise RuntimeError("The AI returned an invalid chat response")
        return {"reply": data["reply"]}
    except GroqPayloadTooLargeError as e:
        raise HTTPException(413, str(e)) from e
    except GroqProviderError as e:
        raise HTTPException(503, str(e)) from e
    except Exception as e:
        raise HTTPException(502, "We couldn't complete this chat right now. Please try again.") from e
