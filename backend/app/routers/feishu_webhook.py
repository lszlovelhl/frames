"""飞书机器人 webhook 接收端点。

用户在飞书群里 @机器人 发消息，飞书会推送到这个端点。
我们转发到 /api/chat 处理，然后返回结果。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.routers.chat import chat, ChatRequest

logger = logging.getLogger(__name__)
router = APIRouter(tags=["feishu_webhook"])


@router.post("/webhook/feishu")
async def feishu_webhook(request: Request):
    """飞书机器人消息接收端点。"""
    body = await request.json()
    logger.info("飞书 webhook 收到消息：%s", str(body)[:200])

    # 飞书 challenge 验证（首次配置时）
    if body.get("type") == "url_verification":
        return {"challenge": body.get("challenge")}

    # 解析消息内容
    event = body.get("event", {})
    message = event.get("message", {})
    content = message.get("content", "{}")

    import json
    try:
        content_data = json.loads(content)
        text = content_data.get("text", "")
    except Exception:
        text = ""

    if not text:
        return {"reply": "没听清，再说一遍？"}

    # 去掉 @机器人 的部分
    text = text.replace("@_user_1", "").strip()

    # 调用 chat 接口处理
    result = await chat(ChatRequest(
        message=text,
        platform="feishu",
    ), db=None)

    return {"reply": result.get("reply", "处理失败")}
