"""会话标题自动生成（AI 原生侧栏体验：告别'最后一条消息原文'）。

- 会话建立后首条用户消息 → 本地 qwen 生成 ≤12 字标题（免费不出机）
- 失败回退：首条消息截断 12 字
- 只对首条消息生成（后续消息不再重算，省 token）
- chat_sessions.title 幂等补列
"""

import logging

from sqlalchemy import text as sa_text

logger = logging.getLogger(__name__)

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
OLLAMA_MODEL = "qwen3.5:latest"


async def ensure_title_schema() -> None:
    """幂等：chat_sessions 补 title 列。"""
    from app.db import engine

    async with engine.connect() as conn:
        has = await conn.execute(sa_text(
            "SELECT 1 FROM pragma_table_info('chat_sessions') WHERE name='title'"
        ))
        if not has.fetchone():
            await conn.execute(sa_text("ALTER TABLE chat_sessions ADD COLUMN title VARCHAR(64)"))
        await conn.commit()


async def _call_qwen(prompt: str, max_tokens: int = 30) -> str:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                OLLAMA_URL,
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "think": False,
                    "options": {"temperature": 0.4, "max_tokens": max_tokens},
                },
            )
            return resp.json().get("response", "").strip()
    except Exception as e:
        logger.warning("标题生成失败：%s", e)
        return ""


async def generate_title(first_message: str) -> str:
    """从首条用户消息生成 ≤12 字标题。"""
    if not first_message:
        return "新会话"
    prompt = (
        f"给这段对话起一个不超过 12 个字的会话标题（小标题风格，可含主题词）。\n"
        f"用户第一条消息：{first_message[:80]}\n"
        f"只输出标题本身，不要引号不要多余文字。"
    )
    title = await _call_qwen(prompt)
    title = title.strip().strip('"').strip("'").strip("「」")
    if len(title) > 20:
        title = title[:20]
    if not title:
        title = first_message.strip()[:12]
    return title


async def maybe_title(session_id: str, first_message: str) -> None:
    """幂等：该会话无标题则生成（内部自建 session，异步调用方不阻塞主流程）。"""
    from app.db import SessionLocal

    try:
        async with SessionLocal() as db:
            row = (await db.execute(sa_text(
                "SELECT title FROM chat_sessions WHERE id=:sid"
            ), {"sid": session_id})).fetchone()
            if row and row[0]:
                return
            title = await generate_title(first_message)
            await db.execute(sa_text(
                "UPDATE chat_sessions SET title=:t, updated_at=CURRENT_TIMESTAMP WHERE id=:sid"
            ), {"t": title, "sid": session_id})
            await db.commit()
            logger.info("会话标题已生成：%s → %s", session_id[:8], title)
    except Exception as e:
        logger.warning("会话标题跳过：%s", e)
