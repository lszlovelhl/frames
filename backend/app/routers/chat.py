"""自然语言对话接口：用户说人话，AI 理解意图并调用对应功能。

各个平台（飞书/微信/网页）都接这一个口子。
"""
from __future__ import annotations

import json
import logging
import re

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session

logger = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    message: str
    user_id: str = "anonymous"
    platform: str = "web"  # web / feishu / wechat / douyin


# 意图识别 prompt
SYSTEM_PROMPT = """你是帧间（Frames）AI 短视频拆解平台的助手。用户会用自然语言和你对话，你需要识别用户意图并提取参数。

当前支持的功能：
1. **拆解视频**：用户想上传/拆解一个视频
   - 意图码：breakdown
   - 参数：url（视频链接）
   - 触发词：拆解、分析、拆一下、帮我看看这个视频、这个视频讲什么、帮我拆
   - 示例输入："帮我拆解 https://v.douyin.com/abc123"
   - 示例输出：{"intent": "breakdown", "url": "https://v.douyin.com/abc123", "video_id": ""}

2. **查拆解结果**：用户想查某个视频的拆解进度或结果
   - 意图码：get_result
   - 参数：video_id（拆解任务 ID）
   - 触发词：结果、进度、拆完了吗、查一下、怎么样了、好了吗、拆好没
   - 示例输入："拆完了吗"
   - 示例输出：{"intent": "get_result", "url": "", "video_id": ""}
   - 注意：如果上下文里有最近的 job_id，video_id 留空，系统会自动用上下文的

3. **找相似视频**：用户想找类似的视频
   - 意图码：similar
   - 参数：video_id
   - 触发词：相似、类似、推荐、还有类似的吗、有没有差不多的
   - 示例输入："还有类似的视频吗"
   - 示例输出：{"intent": "similar", "url": "", "video_id": ""}

4. **帮助**：用户不知道能做什么，或者打招呼
   - 意图码：help
   - 参数：无
   - 触发词：你能做什么、帮助、怎么用、你好、hi、在吗
   - 示例输入："你好"
   - 示例输出：{"intent": "help", "url": "", "video_id": ""}

只返回 JSON，不要其他内容。"""


async def _llm_intent_with_context(message: str, system_prompt: str) -> dict:
    """用 LLM 识别用户意图（带上下文）。"""
    from app.services.two_layer.pipeline import call_flash_llm
    try:
        resp = await call_flash_llm(
            prompt=message,
            system=system_prompt,
            max_tokens=200,
        )
        m = re.search(r'\{.*\}', resp, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception as e:
        logger.warning("意图识别失败：%s", e)
    return {"intent": "help", "url": "", "video_id": ""}


async def _llm_intent(message: str) -> dict:
    """用 LLM 识别用户意图。"""
    return await _llm_intent_with_context(message, SYSTEM_PROMPT)


@router.post("/api/chat")
async def chat(
    req: ChatRequest,
    db: AsyncSession = Depends(get_session),
):
    """自然语言对话入口。各个平台都接这里。"""
    message = req.message.strip()
    if not message:
        return {"reply": "你想说什么？发个视频链接，或者问我拆解结果。"}

    user_id = req.user_id
    platform = req.platform

    # 1. 先做意图识别（纯 LLM，不占 db）
    intent_data = await _llm_intent(message)
    intent = intent_data.get("intent", "help")
    url = intent_data.get("url", "")
    video_id = intent_data.get("video_id", "")

    logger.info("chat: intent=%s, url=%s, video_id=%s", intent, url, video_id)

    # 2. 再开 db 处理上下文和操作
    from sqlalchemy import text as sa_text

    # 兜底：如果 session 状态坏了，先 rollback
    try:
        await db.rollback()
    except Exception:
        pass

    # 找最新 session
    sess_row = (await db.execute(sa_text(
        "SELECT id, last_video_id, last_job_id FROM chat_sessions "
        "WHERE user_id=:uid ORDER BY updated_at DESC LIMIT 1"
    ), {"uid": user_id})).fetchone()

    if sess_row:
        session_id, last_video, last_job = sess_row
        # 如果意图是 get_result/similar，但没提取到 video_id，用上下文的
        if not video_id:
            video_id = last_job or last_video
    else:
        session_id = __import__("uuid").uuid4().hex
        await db.execute(sa_text(
            "INSERT INTO chat_sessions (id, user_id, platform) VALUES (:sid, :uid, :p)"
        ), {"sid": session_id, "uid": user_id, "p": platform})
        last_video, last_job = None, None

    # 存用户消息
    msg_id = __import__("uuid").uuid4().hex
    await db.execute(sa_text(
        "INSERT INTO chat_messages (id, session_id, role, content) VALUES (:mid, :sid, 'user', :content)"
    ), {"mid": msg_id, "sid": session_id, "content": message})

    # 2. 根据意图调用对应功能
    reply_text = ""
    extra = {}

    if intent == "breakdown":
        if not url:
            reply_text = "发个视频链接给我，我帮你拆解。支持抖音/B站/YouTube。"
        else:
            from app.routers.videos import create_video
            try:
                result = await create_video(VideoCreate(url=url), db=db)
                reply_text = "收到！开始拆解这个视频了 🎬\n预计 3-5 分钟出结果，稍后问我「拆完了吗」就能看。"
                extra["job_id"] = result.get("analysis_id")
                # 更新上下文
                await db.execute(sa_text(
                    "UPDATE chat_sessions SET last_job_id=:jid, updated_at=CURRENT_TIMESTAMP WHERE id=:sid"
                ), {"jid": result.get("analysis_id"), "sid": session_id})
            except Exception as e:
                reply_text = f"拆解失败了：{str(e)[:100]}"

    elif intent == "get_result":
        if not video_id:
            reply_text = "告诉我你想查哪个视频，发链接或者视频ID。"
        else:
            try:
                row = (await db.execute(text(
                    "SELECT status, pct, message, script_id FROM breakdown_jobs WHERE id=:jid"
                ), {"jid": video_id})).fetchone()
                if not row:
                    reply_text = "没找到这个拆解任务，确认一下 ID 对不对？"
                else:
                    status, pct, msg, sid = row
                    if status == "done":
                        reply_text = f"✅ 拆解完成！\n进度：100%\n质量分：待查"
                    elif status == "failed":
                        reply_text = f"❌ 拆解失败：{msg or '未知原因'}"
                    else:
                        reply_text = f"⏳ 拆解进行中...\n进度：{pct}%\n阶段：{msg or '处理中'}"
            except Exception as e:
                reply_text = f"查询失败：{str(e)[:100]}"

    elif intent == "similar":
        if not video_id:
            reply_text = "告诉我你想找哪个视频的相似推荐？"
        else:
            try:
                from app.routers.videos import similar_videos
                result = await similar_videos(video_id=video_id, top_k=5, db=db)
                items = result.get("items", [])
                if not items:
                    reply_text = "暂无相似视频推荐（可能只有这一个已拆解视频）"
                else:
                    lines = ["🎬 相似视频推荐："]
                    for i, it in enumerate(items, 1):
                        lines.append(f"{i}. {it.get('title', '无标题')}（相似度 {it.get('score', 0)}）")
                    reply_text = "\n".join(lines)
            except Exception as e:
                reply_text = f"查询相似失败：{str(e)[:100]}"

    else:
        reply_text = (
            "我是帧间 AI 短视频拆解助手 🤖\n"
            "\n"
            "你可以：\n"
            "1. 发视频链接，我帮你拆解分析\n"
            "2. 问我「拆完了吗」，查进度\n"
            "3. 问我「还有类似的吗」，找相似视频\n"
            "\n"
            "试试发个抖音/B站链接？"
        )

    # 3. 存 AI 回复到历史
    ai_msg_id = __import__("uuid").uuid4().hex
    await db.execute(sa_text(
        "INSERT INTO chat_messages (id, session_id, role, content, intent) VALUES (:mid, :sid, 'assistant', :content, :intent)"
    ), {"mid": ai_msg_id, "sid": session_id, "content": reply_text, "intent": intent})

    # 4. 更新 session 时间
    await db.execute(sa_text(
        "UPDATE chat_sessions SET updated_at=CURRENT_TIMESTAMP WHERE id=:sid"
    ), {"sid": session_id})

    await db.commit()

    return {"reply": reply_text, **extra}


# 延迟导入避免循环依赖
from app.routers.videos import VideoCreate
