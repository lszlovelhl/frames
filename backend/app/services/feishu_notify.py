"""飞书通知服务：拆解完成后推送消息到飞书群。

配置：环境变量 FEISHU_WEBHOOK（飞书群机器人 webhook 地址）。
未配置时静默跳过，不影响主流程。
"""
from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

WEBHOOK = os.getenv("FEISHU_WEBHOOK", "").strip()


async def notify_breakdown_done(
    video_title: str,
    script_id: str,
    quality_score: float,
    video_url: str = "",
) -> bool:
    """拆解完成后推送飞书通知。返回是否发送成功。"""
    if not WEBHOOK:
        logger.debug("FEISHU_WEBHOOK 未配置，跳过飞书通知")
        return False

    text = (
        f"✅ 视频拆解完成\n"
        f"标题：{video_title}\n"
        f"质量分：{quality_score}\n"
        f"Script ID：{script_id[:8]}...\n"
    )
    if video_url:
        text += f"链接：{video_url}\n"

    payload = {
        "msg_type": "text",
        "content": {"text": text},
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(WEBHOOK, json=payload)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") == 0:
                logger.info("飞书通知发送成功：script_id=%s", script_id[:8])
                return True
            else:
                logger.warning("飞书通知返回错误：%s", data)
                return False
    except Exception as e:
        logger.warning("飞书通知发送失败：%s", e)
        return False
