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
   - 触发词：拆解、分析、拆一下、帮我看看这个视频、这个视频讲什么
2. **查拆解结果**：用户想查某个视频的拆解进度或结果
   - 意图码：get_result
   - 参数：video_id 或 url
   - 触发词：结果、进度、拆完了吗、查一下
3. **找相似视频**：用户想找类似的视频
   - 意图码：similar
   - 参数：video_id
   - 触发词：相似、类似、推荐、还有类似的吗
4. **帮助**：用户不知道能做什么
   - 意图码：help
   - 参数：无
   - 触发词：你能做什么、帮助、怎么用

只返回 JSON，格式：
{"intent": "意图码", "url": "提取到的视频链接（没有就空字符串）", "video_id": "提取到的视频ID（没有就空字符串）"}
"""


async def _llm_intent(message: str) -> dict:
    """用 LLM 识别用户意图。"""
    from app.services.two_layer.pipeline import call_flash_llm
    try:
        resp = await call_flash_llm(
            prompt=message,
            system=SYSTEM_PROMPT,
            max_tokens=200,
        )
        # 提取 JSON
        m = re.search(r'\{.*\}', resp, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception as e:
        logger.warning("意图识别失败：%s", e)
    return {"intent": "help", "url": "", "video_id": ""}


@router.post("/api/chat")
async def chat(
    req: ChatRequest,
    db: AsyncSession = Depends(get_session),
):
    """自然语言对话入口。各个平台都接这里。"""
    message = req.message.strip()
    if not message:
        return {"reply": "你想说什么？发个视频链接，或者问我拆解结果。"}

    # 1. 意图识别
    intent_data = await _llm_intent(message)
    intent = intent_data.get("intent", "help")
    url = intent_data.get("url", "")
    video_id = intent_data.get("video_id", "")

    logger.info("chat: intent=%s, url=%s, video_id=%s", intent, url, video_id)

    # 2. 根据意图调用对应功能
    if intent == "breakdown":
        if not url:
            return {"reply": "发个视频链接给我，我帮你拆解。支持抖音/B站/YouTube。"}
        # 调用拆解 API
        from app.routers.videos import create_video
        try:
            result = await create_video(VideoCreate(url=url), db=db)
            return {
                "reply": f"收到！开始拆解这个视频了 🎬\n预计 3-5 分钟出结果，稍后问我「拆完了吗」就能看。",
                "job_id": result.get("analysis_id"),
            }
        except Exception as e:
            return {"reply": f"拆解失败了：{str(e)[:100]}"}

    elif intent == "get_result":
        if not video_id and not url:
            return {"reply": "告诉我你想查哪个视频，发链接或者视频ID。"}
        # TODO: 查结果
        return {"reply": "查询结果功能开发中，稍等～"}

    elif intent == "similar":
        return {"reply": "相似推荐功能开发中，稍等～"}

    else:
        return {
            "reply": (
                "我是帧间 AI 短视频拆解助手 🤖\n"
                "\n"
                "你可以：\n"
                "1. 发视频链接，我帮你拆解分析\n"
                "2. 问我「拆完了吗」，查进度\n"
                "3. 问我「还有类似的吗」，找相似视频\n"
                "\n"
                "试试发个抖音/B站链接？"
            )
        }


# 延迟导入避免循环依赖
from app.routers.videos import VideoCreate
