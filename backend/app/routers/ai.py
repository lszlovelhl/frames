"""AI 网关路由：/api/ai/*"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as M
from app.ai import chat
from app.db import get_session
from app.services import billing
from app.services.ai_provider import PROVIDER_TEMPLATES

router = APIRouter(prefix="/api/ai", tags=["ai"])


class ChatMsg(BaseModel):
    role: str = Field(pattern="^(system|user|assistant)$")
    content: str


class ChatReq(BaseModel):
    messages: list[ChatMsg]
    model: str = "flash"  # flash / pro / vision
    provider: str | None = None  # 指定服务商 key，缺省自动路由
    temperature: float | None = None
    max_tokens: int | None = 2048


@router.get("/models")
async def list_models(db: AsyncSession = Depends(get_session)):
    """已接入可用模型：按服务商（填了 key 且启用的）聚合返回。"""
    providers = (
        await db.execute(
            select(M.AiProvider)
            .where(M.AiProvider.enabled.is_(True), M.AiProvider.api_key != "")
            .order_by(M.AiProvider.priority)
        )
    ).scalars().all()
    if not providers:
        # 无任何配置时回退预置模板（提示可接入的服务商）
        out = []
        for k, t in PROVIDER_TEMPLATES.items():
            for m in t["models"]:
                out.append({"provider": k, "provider_name": t["name"], **m})
        return {"items": out, "configured": False}
    out = []
    for p in providers:
        for m in p.models or []:
            out.append({"provider": p.key, "provider_name": p.name, **m})
    return {"items": out, "configured": True}


@router.post("/chat")
async def ai_chat(req: ChatReq, db: AsyncSession = Depends(get_session)):
    """通用对话入口（拆解/创作能力后续在此基础上封装）"""
    acc, points = await billing.precheck(db, "chat")
    try:
        result = await chat(
            messages=[m.model_dump() for m in req.messages],
            model=req.model,
            provider=req.provider,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"AI 网关调用失败: {exc}") from exc
    await billing.consume(
        db, account=acc, action="chat", points=points,
        ref_type=None, ref_id=None, note="通用对话",
    )
    await db.commit()
    return result
