"""自我反思 Reflexion（2026 Reflexion 机制精神，本地轻量落地）。

拆解报告生成后做一次"自己挑自己毛病"的自检：
1. ASR 乱码/口误——脚本里是否有乱码符号、重复字、明显口误
2. 颗粒度——段落数是否过少（漏拆）或过多（碎拆）
3. 三一致——标题 / 核心创意 / 摘要三者是否对得上（跑题检测）
4. 结论可执行性——拆解结论是否落到可执行的剪辑/创作建议

自检不合格（≤6 分或有硬伤）→ 打 quality_flag 标注并在报告里显式提示，
不假装"完美拆解"。用户 2026-10 明确重视拆解质量审查，这就是把审查
交给模型自己做的闭环。

- 模型：本地 qwen3.5（免费不出机）
- 存储：script_script.self_review（JSON：score/issues/verdict）幂等补列
- 接入：拆解完成 mark_done 前异步调用，不阻塞队列
"""

import asyncio
import json
import logging

from sqlalchemy import text as sa_text

logger = logging.getLogger(__name__)

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
OLLAMA_MODEL = "qwen3.5:latest"


async def ensure_self_review_schema() -> None:
    """幂等：script_script 补 self_review（JSON）、quality_flag 列。"""
    from app.db import engine

    async with engine.connect() as conn:
        for col, ddl in (
            ("self_review", "TEXT"),
            ("quality_flag", "VARCHAR(16)"),
        ):
            has = await conn.execute(sa_text(
                "SELECT 1 FROM pragma_table_info('script_script') WHERE name=:n"
            ), {"n": col})
            if not has.fetchone():
                await conn.execute(sa_text(
                    f"ALTER TABLE script_script ADD COLUMN {col} {ddl}"
                ))
        await conn.commit()


async def _call_qwen(prompt: str, max_tokens: int = 500) -> str:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                OLLAMA_URL,
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "think": False,
                    "format": "json",
                    "options": {"temperature": 0.2, "max_tokens": max_tokens},
                },
            )
            return resp.json().get("response", "").strip()
    except Exception as e:
        logger.warning("自检调用失败：%s", e)
        return ""


def _parse_review(raw: str) -> dict:
    """容错解析自检 JSON：优先完整 JSON，失败时退回空结论。"""
    if not raw:
        return {"score": None, "issues": [], "verdict": "自检不可用"}
    try:
        data = json.loads(raw)
        return {
            "score": int(data.get("score", 0)),
            "issues": [str(x) for x in data.get("issues", [])],
            "verdict": str(data.get("verdict", "")),
        }
    except Exception:
        # JSON 解析失败：尝试提取 score 数字
        import re
        m = re.search(r'"score"\s*:\s*(\d+)', raw)
        score = int(m.group(1)) if m else None
        return {"score": score, "issues": ["自检输出未按格式返回"], "verdict": "需人工复核"}


async def run_self_review(db, script_id: str) -> dict:
    """对已生成拆解脚本做一次自检。返回 {score, issues, verdict, flag}。"""
    row = (await db.execute(sa_text(
        "SELECT title, core_idea, summary, quality_score FROM script_script WHERE id=:sid"
    ), {"sid": script_id})).fetchone()
    if not row:
        return {}
    title, core_idea, summary, q = (row[0] or ""), (row[1] or ""), (row[2] or ""), row[3]

    prompt = (
        "你是资深短视频拆解质检员，任务是给一份 AI 拆解报告挑毛病。\n"
        "请严格检查以下四项：\n"
        "1. ASR乱码/口误：拆解摘要里有没有乱码符号、重复字、明显不通顺的句子\n"
        "2. 颗粒度：段落划分是否合理（过少=漏拆，过多=碎拆）\n"
        "3. 三一致：标题、核心创意、摘要是否一致（有没有跑题、编造）\n"
        "4. 可执行性：结论是否具体可执行（剪辑/创作建议），还是空话套话\n\n"
        "输出 JSON（不要多余文字）：\n"
        "{\"score\": 1到10的整数, \"issues\": [\"具体问题1\", \"具体问题2\"], \"verdict\": \"一句话总评\"}\n\n"
        f"待检报告：\n标题：{title[:100]}\n核心创意：{core_idea[:200]}\n摘要：{summary[:400]}"
    )
    raw = await _call_qwen(prompt)
    review = _parse_review(raw)

    # 硬伤判定：分数偏低或明确列出问题 → 不合格
    score = review["score"]
    flag = "ok"
    if score is None:
        flag = "review"
    elif score <= 6 or len(review["issues"]) >= 3:
        flag = "flagged"
    elif score <= 8:
        flag = "minor"

    payload = {
        "score": score,
        "issues": review["issues"][:6],
        "verdict": review["verdict"][:120],
        "model_score": q,
    }
    await db.execute(sa_text(
        "UPDATE script_script SET self_review=:r, quality_flag=:f, updated_at=CURRENT_TIMESTAMP "
        "WHERE id=:sid"
    ), {"r": json.dumps(payload, ensure_ascii=False), "f": flag, "sid": script_id})
    logger.info("自检完成：script=%s score=%s flag=%s", script_id[:8], score, flag)
    payload["flag"] = flag
    return payload


async def run_self_review_if_new(db, script_id: str) -> None:
    """拆解完成钩子：已有自检结果则跳过（幂等），否则执行。"""
    try:
        row = (await db.execute(sa_text(
            "SELECT self_review FROM script_script WHERE id=:sid"
        ), {"sid": script_id})).fetchone()
        if row and row[0]:
            return
        await run_self_review(db, script_id)
    except Exception as e:
        logger.warning("自检跳过（不阻塞拆解）：%s", e)
