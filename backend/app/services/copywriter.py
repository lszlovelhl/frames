"""多步工作流（LangGraph plan→act 精神，第 1 个实用场景落地）。

"帮我拆解这个视频并写篇小红书文案" → 拆解任务完成时自动生成文案。
- 用户请求拆解时若提到"文案/小红书/总结"等词，_handle_breakdown 会写
  app_meta pending_article:{job_id} 标记（plan）
- breakdown_jobs.process 在 mark_done 后调用 maybe_generate_article（act），
  自动生成小红书文案并存 app_meta article:{job_id}
- 用户随后问"文案好了吗"即可取到（get_result 联动）
文案生成用本地 qwen3.5（免费不出机），成本为零。
"""

import json
import logging

from sqlalchemy import text as sa_text

logger = logging.getLogger(__name__)

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
OLLAMA_MODEL = "qwen3.5:latest"


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
                    "options": {"temperature": 0.7, "max_tokens": max_tokens},
                },
            )
            return resp.json().get("response", "").strip()
    except Exception as e:
        logger.warning("文案生成失败：%s", e)
        return ""


async def _fetch_script(db, script_id: str):
    row = (await db.execute(sa_text(
        "SELECT title, core_idea, summary FROM script_script WHERE id=:sid"
    ), {"sid": script_id})).fetchone()
    if not row:
        return None
    return {"title": row[0], "core_idea": row[1], "summary": row[2]}


async def generate_article(script_id: str, video_title: str = "") -> str:
    """根据拆解报告生成小红书风格文案。返回文案文本。"""
    from app.db import SessionLocal

    try:
        async with SessionLocal() as db:
            script = await _fetch_script(db, script_id)
            if not script:
                return ""
            title = script["title"] or video_title or "这个视频"
            idea = (script["core_idea"] or "")[:200]
            summary = (script["summary"] or "")[:400]
            prompt = (
                "你是资深小红书内容运营。根据视频拆解报告，写一篇能爆的小红书图文文案。\n"
                f"视频标题：{title}\n"
                f"核心观点：{idea}\n"
                f"拆解摘要：{summary}\n\n"
                "要求：\n"
                "1. 标题 1 条，抓眼球不标题党\n"
                "2. 正文 100-180 字，口语化、有钩子、有干货点\n"
                "3. 结尾 4-6 个话题标签（#）\n"
                "格式：标题/正文/标签，用空行分隔。"
            )
            return await _call_qwen(prompt)
    except Exception as e:
        logger.warning("文案读取拆解失败：%s", e)
        return ""


async def maybe_generate_article(db, job, script_id: str | None) -> bool:
    """拆解完成联动：用户要求过文案则自动生成并存 app_meta。返回是否生成。"""
    if not script_id:
        return False
    try:
        rec = (await db.execute(sa_text(
            "SELECT value FROM app_meta WHERE key=:k"
        ), {"k": f"pending_article:{job.id}"})).fetchone()
        if not rec:
            return False
        # 已有文案则跳过（幂等）
        existing = (await db.execute(sa_text(
            "SELECT 1 FROM app_meta WHERE key=:k"
        ), {"k": f"article:{job.id}"})).fetchone()
        if existing:
            await db.execute(sa_text("DELETE FROM app_meta WHERE key=:k"),
                             {"k": f"pending_article:{job.id}"})
            await db.commit()
            return True
        article = await generate_article(script_id)
        if not article:
            return False
        await db.execute(sa_text(
            "INSERT INTO app_meta (key, value) VALUES (:k, :v) "
            "ON CONFLICT(key) DO UPDATE SET value=:v"
        ), {"k": f"article:{job.id}", "v": article})
        await db.execute(sa_text("DELETE FROM app_meta WHERE key=:k"),
                         {"k": f"pending_article:{job.id}"})
        await db.commit()
        logger.info("多步工作流：job %s 文案已自动生成", job.id[:8])
        return True
    except Exception as e:
        logger.warning("文案自动生成失败：%s", e)
        return False


async def get_article(db, job_id: str) -> str:
    """读取已生成的文案。"""
    rec = (await db.execute(sa_text(
        "SELECT value FROM app_meta WHERE key=:k"
    ), {"k": f"article:{job_id}"})).fetchone()
    return rec[0] if rec else ""
