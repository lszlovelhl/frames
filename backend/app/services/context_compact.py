"""上下文压缩（LangChain compaction 精神，2026 机制取经落地）。

长会话自动总结成要点，存 chat_sessions.summary；
后续对话把摘要注入 AI 上下文——AI 记得长对话前文，不把全部历史塞进 prompt，
省 token 且不爆上下文。

- 触发条件：会话消息数 >= 16，且距上次压缩 >= 10 条新消息
- 模型：本地 qwen3.5（免费、不出机），think:false 直接输出
- 执行：后台异步任务（asyncio.create_task），不拖慢用户响应
"""

import asyncio
import logging

from sqlalchemy import text as sa_text

logger = logging.getLogger(__name__)

SUMMARY_THRESHOLD = 16      # 消息达到该条数触发首次压缩
INCREMENT = 10              # 距上次压缩新增多少条才再压
MAX_FEED_MESSAGES = 40      # 摘要最多吃最近 40 条消息
SUMMARY_TARGET_LINES = 8    # 摘要要点目标条数

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
OLLAMA_MODEL = "qwen3.5:latest"


async def ensure_summary_schema() -> None:
    """幂等：给 chat_sessions 补 summary 列（SQLite ALTER 无 IF NOT EXISTS）。"""
    from app.db import engine

    async with engine.connect() as conn:
        rows = await conn.execute(sa_text("PRAGMA table_info(chat_sessions)"))
        cols = {r[1] for r in rows.fetchall()}
        if "summary" not in cols:
            await conn.execute(sa_text("ALTER TABLE chat_sessions ADD COLUMN summary TEXT DEFAULT ''"))
        if "summarized_cnt" not in cols:
            await conn.execute(sa_text("ALTER TABLE chat_sessions ADD COLUMN summarized_cnt INTEGER DEFAULT 0"))
        await conn.commit()


async def _session_stats(session, session_id: str) -> tuple[int, int]:
    """返回 (消息总数, 上次压缩时条数)。"""
    row = (await session.execute(sa_text(
        "SELECT summarized_cnt FROM chat_sessions WHERE id=:sid"
    ), {"sid": session_id})).fetchone()
    cnt_row = (await session.execute(sa_text(
        "SELECT count(*) FROM chat_messages WHERE session_id=:sid"
    ), {"sid": session_id})).fetchone()
    total = cnt_row[0] if cnt_row else 0
    prev = row[0] if row and row[0] else 0
    return total, prev


async def _call_qwen(prompt: str, max_tokens: int = 300) -> str:
    """本地 qwen3.5 生成（think:false 直接输出，免费不出机）。"""
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
                    "options": {"temperature": 0.3, "max_tokens": max_tokens},
                },
            )
            return resp.json().get("response", "").strip()
    except Exception as e:
        logger.warning("摘要生成失败：%s", e)
        return ""


async def summarize_session(session_id: str, user_id: str) -> None:
    """后台任务：把会话最近消息总结成要点并入库。"""
    from app.db import SessionLocal

    try:
        async with SessionLocal() as session:
            rows = (await session.execute(sa_text(
                "SELECT role, content FROM chat_messages "
                "WHERE session_id=:sid ORDER BY created_at DESC LIMIT :lim"
            ), {"sid": session_id, "lim": MAX_FEED_MESSAGES})).fetchall()
            if len(rows) < SUMMARY_THRESHOLD:
                return

            # 正序拼接（旧的在前）
            feed = "\n".join(
                f"{'用户' if r[0] == 'user' else 'AI'}：{r[1][:300]}" for r in reversed(rows)
            )
            prompt = (
                f"这是帧间 AI 助手和用户的一段对话记录。"
                f"请把它压缩成 {SUMMARY_TARGET_LINES} 条以内的要点，覆盖："
                f"用户偏好/职业/兴趣、聊过或拆解过的视频、拆解结论、用户明确要求、未完成事项。\n\n"
                f"对话记录：\n{feed}\n\n"
                f"只输出要点清单，每行一条，不要开场白。"
            )
            summary = await _call_qwen(prompt)
            if not summary:
                return
            await session.execute(sa_text(
                "UPDATE chat_sessions SET summary=:s, summarized_cnt=:c, "
                "updated_at=CURRENT_TIMESTAMP WHERE id=:sid AND user_id=:uid"
            ), {"s": summary, "c": len(rows), "sid": session_id, "uid": user_id})
            await session.commit()
            logger.info("会话 %s 已压缩，要点 %d 字", session_id[:8], len(summary))
    except Exception as e:
        logger.warning("后台压缩失败：%s", e)


def schedule_summarize(session_id: str, user_id: str, db_session) -> None:
    """在对话响应完成后调用：满足条件就异步起压缩任务（不阻塞响应）。"""
    try:
        import asyncio

        asyncio.get_running_loop().create_task(summarize_session(session_id, user_id))
    except Exception as e:
        logger.warning("压缩任务调度失败：%s", e)


async def need_summarize(db_session, session_id: str) -> bool:
    """轻量检查：当前是否满足压缩条件（供 chat 主流程在 commit 前调用）。"""
    try:
        total, prev = await _session_stats(db_session, session_id)
        return total >= SUMMARY_THRESHOLD and (total - prev) >= INCREMENT
    except Exception:
        return False
