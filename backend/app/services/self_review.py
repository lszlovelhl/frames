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
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text as sa_text

logger = logging.getLogger(__name__)

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
OLLAMA_MODEL = "qwen3.5:latest"

# 质量门定向修订：本地免费模型，整闭环强制 1 轮（防 token 失控；禁止切 pro/DeepSeek）
GATE_REVISION_MAX_ROUNDS = 1
GATE_REVISION_MIN_LEN = 30


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
        review = await run_self_review(db, script_id)
        await db.commit()
        # Reflexion act 环节：flagged → 自动修订拆解摘要（补颗粒度/可执行性）
        if review.get("flag") == "flagged":
            await regenerate_summary_if_flagged(db, script_id, review)
    except Exception as e:
        logger.warning("自检跳过（不阻塞拆解）：%s", e)


def _coerce_plain_text(raw: str, limit: int = 400) -> str:
    """把模型偶发返回的代码围栏 / JSON 片段压成纯文本（摘要类字段专用）。"""
    text = (raw or "").strip()
    if not text:
        return ""
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    if text.startswith("{") or text.startswith("["):
        try:
            parsed = json.loads(text)
            vals: list[str] = []
            if isinstance(parsed, dict):
                for v in parsed.values():
                    if isinstance(v, str):
                        vals.append(v)
                    elif isinstance(v, (list, dict)):
                        vals.append(str(v))
            elif isinstance(parsed, list):
                vals = [str(v) for v in parsed if isinstance(v, (str, dict, list))]
            text = "".join(vals) if vals else text
        except Exception:  # noqa: BLE001
            pass
    return text[:limit].strip()


async def regenerate_summary_if_flagged(db, script_id: str, review: dict) -> None:
    """自检不合格时，基于问题清单自动重写拆解摘要（修订而非假装完美）。"""
    try:
        row = (await db.execute(sa_text(
            "SELECT title, core_idea, summary FROM script_script WHERE id=:sid"
        ), {"sid": script_id})).fetchone()
        if not row:
            return
        title, core_idea, summary = (row[0] or ""), (row[1] or ""), (row[2] or "")
        issues = "；".join(review.get("issues", [])[:4])
        prompt = (
            "你是资深短视频拆解专家。上一版拆解被质检挑出了这些问题：\n"
            f"{issues}\n\n"
            "请针对这些问题重写一份『拆解摘要』（100-180 字）：\n"
            "要求：按起-承-转-合补足段落颗粒度，给出可执行的剪辑/创作建议，"
            "杜绝空话套话，与标题和核心创意严格一致。\n\n"
            f"标题：{title[:80]}\n核心创意：{core_idea[:200]}\n原摘要：{summary[:300]}\n\n"
            "只输出重写后的摘要正文。"
        )
        new_summary = await _call_qwen(prompt, max_tokens=400)
        if not new_summary or len(new_summary) < 30:
            return
        # 清洗：模型偶发输出 JSON/围栏片段 → 统一转纯文本摘要
        new_summary = _coerce_plain_text(new_summary, limit=400)
        # 更新摘要并记录修订痕迹（不假装重拆，透明标注）
        await db.execute(sa_text(
            "UPDATE script_script SET summary=:s, self_review=:r, updated_at=CURRENT_TIMESTAMP WHERE id=:sid"
        ), {
            "s": new_summary,
            "r": json.dumps({
                **review,
                "regenerated": True,
                "regenerated_at": datetime.now(UTC).isoformat(),
                "prev_summary": summary[:120],
            }, ensure_ascii=False),
            "sid": script_id,
        })
        await db.commit()
        logger.info("自检 flagged → 摘要已自动修订：script=%s", script_id[:8])
    except Exception as e:
        logger.warning("摘要修订失败（不影响拆解）：%s", e)


async def revise_summary_on_gate(db, script_id: str, gate: dict) -> dict:
    """质量门未通过时的一轮定向修订（本地免费 qwen，整闭环强制 1 轮）。

    范围克制：只重写 ``summary``（颗粒度 / 可执行性短板）；钩子缺失、段落覆盖、
    积木为空等结构类问题不在这里反复调模型，而是经 ``gate["fixes"]`` 写入证据，
    供下一轮拆解或人工复核处理。模型固定 ``OLLAMA_MODEL``（本地免费档），
    绝不切 pro / DeepSeek。
    """
    result: dict[str, Any] = {
        "ok": False,
        "rounds": 0,
        "model": OLLAMA_MODEL,
        "issues_addressed": [],
        "reason": "",
    }
    try:
        row = (await db.execute(sa_text(
            "SELECT title, core_idea, summary, self_review FROM script_script WHERE id=:sid"
        ), {"sid": script_id})).fetchone()
        if not row:
            result["reason"] = "script 记录不存在"
            return result
        title, core_idea, summary, prev_self = row[0] or "", row[1] or "", row[2] or "", row[3]

        issues = [str(i) for i in (gate.get("issues") or [])][:6]
        fixes = [str(f) for f in (gate.get("fixes") or [])][:6]
        result["issues_addressed"] = issues
        prompt = (
            "你是资深短视频拆解质检员兼修订员。这份拆解报告质量门未通过，"
            "问题与修订方向如下。请只重写『拆解摘要』（100~180 字）："
            "针对问题补足起承转合颗粒度与可执行的剪辑/创作建议，"
            "与标题和核心创意严格一致，杜绝空话套话；不要输出 JSON 或解释。\n\n"
            "【未通过项】\n" + "\n".join(f"- {i}" for i in issues) + "\n"
            "【修订方向】\n" + "\n".join(f"- {f}" for f in fixes) + "\n\n"
            f"标题：{title[:80]}\n核心创意：{core_idea[:200]}\n原摘要：{summary[:300]}"
        )
        raw = await _call_qwen(prompt, max_tokens=600)
        new_summary = _coerce_plain_text(raw, limit=400)
        if len(new_summary) < GATE_REVISION_MIN_LEN:
            result["reason"] = f"修订摘要过短（{len(new_summary)} 字 < {GATE_REVISION_MIN_LEN}）"
            return result

        try:
            merged = json.loads(prev_self) if prev_self else {}
        except Exception:  # noqa: BLE001
            merged = {}
        merged["gate_revision"] = {
            "rounds": GATE_REVISION_MAX_ROUNDS,
            "model": OLLAMA_MODEL,
            "gate_score": gate.get("score"),
            "gate_passed_before": bool(gate.get("passed")),
            "issues": issues,
            "prev_len": len((summary or "").strip()),
            "new_len": len(new_summary),
            "regenerated_at": datetime.now(UTC).isoformat(),
        }
        await db.execute(sa_text(
            "UPDATE script_script SET summary=:s, self_review=:r, updated_at=CURRENT_TIMESTAMP WHERE id=:sid"
        ), {
            "s": new_summary,
            "r": json.dumps(merged, ensure_ascii=False),
            "sid": script_id,
        })
        result.update(
            ok=True,
            rounds=GATE_REVISION_MAX_ROUNDS,
            prev_len=len((summary or "").strip()),
            new_len=len(new_summary),
        )
        logger.info("质量门定向修订完成：script=%s gate_score=%s", str(script_id)[:8], gate.get("score"))
        return result
    except Exception as e:  # noqa: BLE001
        result["reason"] = f"{type(e).__name__}: {str(e)[:140]}"
        logger.warning("质量门定向修订失败（不阻塞拆解主流程）：%s", e)
        return result
