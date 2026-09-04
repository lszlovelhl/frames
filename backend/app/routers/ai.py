"""AI 网关路由：/api/ai/*"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.ai import MODELS, chat

router = APIRouter(prefix="/api/ai", tags=["ai"])


class ChatMsg(BaseModel):
    role: str = Field(pattern="^(system|user|assistant)$")
    content: str


class ChatReq(BaseModel):
    messages: list[ChatMsg]
    model: str = "flash"  # flash / pro / vision
    temperature: float | None = None
    max_tokens: int | None = 2048


@router.get("/models")
async def list_models():
    return [{"alias": alias, "model": model_id} for alias, model_id in MODELS.items()]


@router.post("/chat")
async def ai_chat(req: ChatReq):
    """通用对话入口（拆解/创作能力后续在此基础上封装）"""
    try:
        result = await chat(
            messages=[m.model_dump() for m in req.messages],
            model=req.model,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"AI 网关调用失败: {exc}") from exc
    return result
