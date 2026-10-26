"""飞书机器人 webhook 接收端点（完善版）。

用户在飞书里 @机器人 发消息 → 飞书推送本端点 → 转 /api/chat 处理 →
同步返回回复（飞书侧可配置）。核心完善：
- 注入真实 db（修复旧版 db=None 崩溃）
- 飞书 open_id ↔ 本地用户自动建号/复用 → 每个飞书用户独立记忆（多账号隔离落地 IM）
- 按 open_id + platform=feishu 复用/新建独立会话（平台会话隔离）
- 主动回发消息：配置 FEISHU_APP_ID / APP_SECRET 后走即时消息 API；
  未配置时返回 webhook 同步响应并打日志（不影响本地联调）
"""
from __future__ import annotations

import json
import logging
import os
import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import text as sa_text

from app.db import AsyncSession, get_session
from app.routers.chat import chat, ChatRequest

logger = logging.getLogger(__name__)
router = APIRouter(tags=["feishu_webhook"])


@router.post("/webhook/feishu")
async def feishu_webhook(request: Request, db: AsyncSession = Depends(get_session)):
    """飞书机器人消息接收端点（challenge 验证 + 消息处理）。"""
    body = await request.json()
    logger.info("飞书 webhook 收到事件：%s", str(body)[:240])

    # 飞书 challenge 验证（首次配置 URL 时飞书会发 type=url_verification）
    if body.get("type") == "url_verification":
        return {"challenge": body.get("challenge")}

    event = body.get("event", {})
    # 只处理消息事件
    if event.get("type") != "message":
        return {"reply": "ok"}

    # 解析发送者 open_id（飞书 v2 事件：sender.sender_id.open_id）
    sender = event.get("sender", {})
    open_id = sender.get("sender_id", {}).get("open_id") or sender.get("open_id", "")
    # 消息内容：飞书推 content 为 JSON 字符串 {"text":"..."}
    msg = event.get("message", {})
    content = msg.get("content", "{}")
    try:
        content_data = json.loads(content) if isinstance(content, str) else (content or {})
        text = (content_data.get("text") or "").strip()
    except Exception:
        text = ""
    # 群聊里去掉 @机器人 部分
    text = text.replace("@_user_1", "").replace("@_user_2", "").strip()
    if not text:
        return {"reply": "没听清，再说一遍？"}

    if not open_id:
        logger.warning("飞书消息缺 open_id，无法隔离用户")
        return {"reply": "消息缺少用户身份，请重试"}

    # 用户映射：open_id → 本地用户（自动建号，多账号隔离记忆）
    user_id = await _get_or_create_feishu_user(db, open_id)
    # 平台会话隔离：每个 open_id 在 feishu 平台复用最近会话
    session_id = await _get_or_create_feishu_session(db, user_id)

    try:
        result = await chat(
            ChatRequest(message=text, user_id=user_id, platform="feishu", session_id=session_id),
            db=db,
            authorization="",
        )
        reply = result.get("reply", "处理失败")
    except Exception as e:
        logger.exception("飞书消息处理失败")
        reply = f"处理出错了：{str(e)[:80]}"

    # 配置了飞书机器人凭证则主动回发（未配置时 webhook 同步返回，本地可联调）
    if os.getenv("FEISHU_APP_ID") and os.getenv("FEISHU_APP_SECRET"):
        try:
            await _push_feishu_reply(open_id, text, reply)
            return {"reply": "ok"}
        except Exception as e:
            logger.warning("飞书主动回发失败（已退回同步返回）：%s", e)
    return {"reply": reply}


async def _get_or_create_feishu_user(db: AsyncSession, open_id: str) -> str:
    """open_id → 本地用户 id（存在复用，不存在自动建号）。"""
    row = (await db.execute(sa_text(
        "SELECT id FROM users WHERE username=:u"
    ), {"u": open_id})).fetchone()
    if row:
        return row[0]
    new_id = uuid.uuid4().hex
    await db.execute(sa_text(
        "INSERT INTO users (id, username, display_name, role, platforms, password_hash, auth_token) "
        "VALUES (:id, :u, :dn, 'user', :pf, '', :tok)"
    ), {"id": new_id, "u": open_id, "dn": f"飞书用户-{open_id[-6:]}", "pf": json.dumps(["feishu"]), "tok": uuid.uuid4().hex})
    await db.commit()
    logger.info("飞书用户自动建号：%s", open_id[:24])
    return new_id


async def _get_or_create_feishu_session(db: AsyncSession, user_id: str) -> str:
    """该用户 feishu 平台最近会话；无则新建。"""
    row = (await db.execute(sa_text(
        "SELECT id FROM chat_sessions WHERE user_id=:uid AND platform='feishu' "
        "ORDER BY updated_at DESC LIMIT 1"
    ), {"uid": user_id})).fetchone()
    if row:
        return row[0]
    new_id = uuid.uuid4().hex
    await db.execute(sa_text(
        "INSERT INTO chat_sessions (id, user_id, platform) VALUES (:id, :uid, 'feishu')"
    ), {"id": new_id, "uid": user_id})
    await db.commit()
    return new_id


async def _push_feishu_reply(open_id: str, original: str, reply: str) -> None:
    """用飞书即时消息 API 主动回发（需要机器人凭证环境变量）。"""
    import httpx

    app_id = os.getenv("FEISHU_APP_ID")
    app_secret = os.getenv("FEISHU_APP_SECRET")
    async with httpx.AsyncClient(timeout=15) as client:
        token_resp = await client.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
        )
        token = token_resp.json().get("tenant_access_token")
        if not token:
            raise RuntimeError("飞书 token 获取失败")
        await client.post(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "receive_id": open_id,
                "msg_type": "text",
                "content": json.dumps({"text": reply}, ensure_ascii=False),
            },
        )
    logger.info("飞书回发成功：open_id=%s reply=%s", open_id[:16], reply[:40])
