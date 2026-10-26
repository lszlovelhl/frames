"""多步工作流 plan→act 落地（LangGraph 精神）：拆解完成 → 自动生成文案。

场景：用户说"帮我拆解这个视频，并写成小红书文案"。
- 第一步（plan）：chat 识别复合意图，挂待办 copywriting_pending:{video_id}
- 第二步（act）：后台拆解完成后 worker 检测到待办，自动调本地 qwen
  从拆解脚本生成小红书文案，存 copywriting_results
- 用户随后问"拆完了吗/文案好了吗"即可拿到文案

模型：本地 qwen3.5（免费不出机）；全流程异步后台，不阻塞拆解队列。
"""

import asyncio
import logging
import uuid

from sqlalchemy import text as sa_text

logger = logging.getLogger(__name__)

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
OLLAMA_MODEL = "qwen3.5:latest"
PENDING_PREFIX = "copywriting_pending:"


async def ensure_copywriting_schema() -> None:
    """幂等：建 copywriting_results 表。"""
    from app.db import engine

    async with engine.connect() as conn:
        await conn.execute(sa_text(
            "CREATE TABLE IF NOT EXISTS copywriting_results ("
            " id TEXT PRIMARY KEY,"
            " video_id TEXT NOT NULL,"
            " script_id TEXT NOT NULL,"
            " style TEXT NOT NULL DEFAULT '小红书',"
            " content TEXT NOT NULL,"
            " created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        ))
        await conn.commit()


async def set_pending(db, video_id: str, style: str = "小红书") -> None:
    """挂文案待办（app_meta，幂等覆盖）。"""
    await db.execute(sa_text(
        "INSERT INTO app_meta (key, value) VALUES (:k, :v) "
        "ON CONFLICT(key) DO UPDATE SET value=:v"
    ), {"k": PENDING_PREFIX + video_id, "v": style})


async def _get_pending(db, video_id: str) -> str | None:
    row = (await db.execute(sa_text(
        "SELECT value FROM app_meta WHERE key=:k"
    ), {"k": PENDING_PREFIX + video_id})).fetchone()
    return row[0] if row else None


async def _clear_pending(db, video_id: str) -> None:
    await db.execute(sa_text(
        "DELETE FROM app_meta WHERE key=:k"
    ), {"k": PENDING_PREFIX + video_id})


async def _call_qwen(prompt: str, max_tokens: int = 400) -> str:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(
                OLLAMA_URL,
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "think": False,
                    "options": {"temperature": 0.8, "max_tokens": max_tokens},
                },
            )
            return resp.json().get("response", "").strip()
    except Exception as e:
        logger.warning("文案生成失败：%s", e)
        return ""


async def _generate_from_script(db, video_id: str, script_id: str, style: str) -> bool:
    """从拆解脚本生成文案并入库。返回是否成功。"""
    row = (await db.execute(sa_text(
        "SELECT title, core_idea, summary FROM script_script WHERE id=:sid"
    ), {"sid": script_id})).fetchone()
    if not row:
        return False
    title, core_idea, summary = (row[0] or ""), (row[1] or ""), (row[2] or "")
    seed = f"标题：{title}\n核心创意：{core_idea}\n拆解摘要：{summary[:500]}"
    prompt = (
        f"你是爆款短视频内容策划。基于下面的视频拆解信息，写一条「{style}」风格的发布文案。\n"
        f"要求：有吸引人的开头钩子、2-4 个亮点小段、结尾互动引导；"
        f"贴合原视频风格；80-150 字，不要 AI 味，不要'首先/其次'，口语自然。\n\n"
        f"拆解信息：\n{seed}\n\n"
        f"只输出文案正文。"
    )
    content = await _call_qwen(prompt)
    if not content:
        return False
    await db.execute(sa_text(
        "INSERT INTO copywriting_results (id, video_id, script_id, style, content) "
        "VALUES (:mid, :vid, :sid, :st, :c)"
    ), {"mid": uuid.uuid4().hex, "vid": video_id, "sid": script_id, "st": style, "c": content})
    logger.info("文案已生成：video=%s style=%s", video_id[:8], style)
    return True


async def run_copywriting_if_pending(db, video_id: str, script_id: str) -> None:
    """拆解完成后调用：有文案待办则生成并清理。"""
    try:
        style = await _get_pending(db, video_id)
        if not style:
            return
        ok = await _generate_from_script(db, video_id, script_id, style)
        await _clear_pending(db, video_id)
        if not ok:
            logger.warning("文案生成未完成（模型/数据问题），待办已清理防死循环")
    except Exception as e:
        logger.warning("文案联动失败：%s", e)


async def get_copywriting(db, video_id: str) -> str | None:
    """查某视频的已生成文案（供 get_result 附带展示）。"""
    row = (await db.execute(sa_text(
        "SELECT content FROM copywriting_results WHERE video_id=:vid "
        "ORDER BY created_at DESC LIMIT 1"
    ), {"vid": video_id})).fetchone()
    return row[0] if row else None
