"""记忆自动演化流水线（Mem0 retrieve/store node 精神，2026 机制取经落地）。

每轮对话结束后自动判断是否有值得沉淀的用户偏好/事实，
有则写入短期记忆（feedback），随后由记忆结晶（dream，memory_dream）向中期/长期演化。
完全本地模型（qwen3.5）判断，免费不出机，不依赖用户显式"记住"指令。

- 触发：对话类意图（chat/followup/find_content）+ 用户消息有实质内容（>= 8 字符）
- 判断：本地 qwen 从 (用户消息, AI 回复) 提取偏好/事实，最多 2 条，无则 NONE
- 去重：向量查重，与已有记忆相似则跳过（防垃圾堆叠）
- 执行：后台异步（asyncio.create_task），不阻塞用户响应
"""

import asyncio
import logging

from sqlalchemy import text as sa_text

logger = logging.getLogger(__name__)

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
OLLAMA_MODEL = "qwen3.5:latest"
MIN_MSG_LEN = 8          # 用户消息达到该长度才尝试演化
SIM_THRESHOLD = 0.86     # 与已有记忆的余弦相似度超过该值则视为重复


async def _call_qwen(prompt: str, max_tokens: int = 120) -> str:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                OLLAMA_URL,
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "think": False,
                    "options": {"temperature": 0.2, "max_tokens": max_tokens},
                },
            )
            return resp.json().get("response", "").strip()
    except Exception as e:
        logger.warning("演化判断失败：%s", e)
        return ""


async def _extract_worth(user_msg: str, ai_reply: str) -> list[str]:
    """用本地 qwen 判断本轮对话有无值得长期记住的偏好/事实。"""
    prompt = (
        "你是记忆管家。下面是一段短视频拆解助手与用户的对话。"
        "请提取**值得长期记住**的用户信息：稳定的偏好、习惯、身份、目标、明确要求。"
        "排除：一次性提问、临时闲聊、AI 自己的客套话。\n\n"
        f"用户：{user_msg[:200]}\n"
        f"AI：{ai_reply[:200]}\n\n"
        "要求：最多输出 2 条，每条一句话、以用户为视角（如'用户喜欢口播类视频'）；"
        "没有值得记的只输出 NONE。"
    )
    resp = await _call_qwen(prompt)
    lines = [l.strip().lstrip("-•*0123456789. ") for l in resp.splitlines() if l.strip()]
    out = [l for l in lines if l and l != "NONE"]
    return out[:2]


async def _is_dup(db, user_id: str, content: str) -> bool:
    """向量查重：与已有记忆相似则跳过。"""
    try:
        from app.services.memory_search import find_similar_memory

        sim = await find_similar_memory(db, content, user_id=user_id)
        return sim and sim.get("score", 0) >= SIM_THRESHOLD
    except Exception:
        return False


async def evolve_turn(user_id: str, user_msg: str, ai_reply: str) -> None:
    """后台任务：判断本轮对话 → 写入值得沉淀的记忆（短期 feedback 起步）。"""
    if not user_msg or len(user_msg.strip()) < MIN_MSG_LEN:
        return
    try:
        from app.db import SessionLocal

        items = await _extract_worth(user_msg, ai_reply)
        if not items:
            return
        async with SessionLocal() as db:
            from app.services.memory_search import remember

            written = 0
            for item in items:
                if await _is_dup(db, user_id, item):
                    continue
                await remember(db, user_id, item, "feedback", 4)
                written += 1
            await db.commit()
            if written:
                logger.info("记忆演化：本轮沉淀 %d 条（%s）", written, "；".join(items)[:60])
    except Exception as e:
        logger.warning("记忆演化失败：%s", e)


def schedule_evolve(user_id: str, user_msg: str, ai_reply: str) -> None:
    """在对话响应后调用：满足条件则后台异步演化记忆（不阻塞响应）。"""
    try:
        asyncio.get_running_loop().create_task(evolve_turn(user_id, user_msg, ai_reply))
    except Exception as e:
        logger.warning("演化任务调度失败：%s", e)
