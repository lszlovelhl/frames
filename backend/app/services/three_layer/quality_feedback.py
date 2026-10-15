"""评估闭环（P0 质量提升·三件套之三）

把质量校验/打分结果接回主流程：拆解完成后，由 pipeline 将 validation 统计写回
breakdown_jobs.evidence（已在 修复4 完成），本模块提供主流程侧的质量收集/汇总/
反馈入口，避免评估只跑在测试里。

- collect_job_quality：按 job 聚合拆解产出质量（script + 六张 lib_* 积木表），
  供接口（如 /api/videos/{id}/quality）与后续反馈回写使用。
- quality_overview：最近拆解任务的质量看板（只读）。
- summarize_lib_rows：纯函数聚合，方便测试与前端复用。

对齐约束：
- 只读聚合，不修改任何表；回写反馈仍由 pipeline 的 evidence 通道负责。
- 质量口径与 validate.py 一致：quality_score ∈ [0,100]，<60 视为 draft（不合格）。
- 六张积木表：lib_topic / lib_hook / lib_copywriting / lib_quote / lib_method / lib_combo，
  均以 source_video_id 隔离（与 pipeline 写库口径一致，String 格式）。
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as M

logger = logging.getLogger(__name__)

# 六张积木表（L1~L6 落库表）的模型类 + 展示名
_LIB_MODELS: tuple[tuple[type[M.Base], str], ...] = (
    (M.LibTopic, "topic"),
    (M.LibHook, "hook"),
    (M.LibCopywriting, "copywriting"),
    (M.LibQuote, "quote"),
    (M.LibMethod, "method"),
    (M.LibCombo, "combo"),
)

DRAFT_THRESHOLD = 60  # 与 validate.py 一致：quality_score < 60 → draft


def summarize_lib_rows(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """纯函数：聚合积木行质量分布（供 collect_job_quality / 测试复用）。

    输入行字段：table(str), quality_score(Number|None), review_status(str|None)。
    输出：{total, scored, draft, accepted, avg_score, min_score, by_table}
    """
    total = len(rows)
    scored = [r for r in rows if r.get("quality_score") is not None]
    draft = [
        r for r in scored if float(r["quality_score"]) < DRAFT_THRESHOLD
    ]
    accepted = [
        r for r in rows if (r.get("review_status") or "") == "accepted"
    ]
    avg_score = (
        round(sum(float(r["quality_score"]) for r in scored) / len(scored), 1)
        if scored
        else None
    )
    min_score = min(float(r["quality_score"]) for r in scored) if scored else None
    by_table: dict[str, dict[str, int | float | None]] = {}
    for r in rows:
        table = r.get("table", "?")
        b = by_table.setdefault(table, {"count": 0, "draft": 0, "scored": 0, "avg_score": None})
        b["count"] += 1
        if r.get("quality_score") is not None:
            b["scored"] += 1
            s = float(r["quality_score"])
            if s < DRAFT_THRESHOLD:
                b["draft"] += 1
            prev = b.get("avg_score")
            b["avg_score"] = s if prev is None else round((float(prev) + s) / 2, 1)
    return {
        "total": total,
        "scored": len(scored),
        "draft": len(draft),
        "accepted": len(accepted),
        "avg_score": avg_score,
        "min_score": min_score,
        "by_table": by_table,
    }


def _row_table_label(model_cls: type[M.Base]) -> str:
    for cls, label in _LIB_MODELS:
        if cls is model_cls:
            return label
    return model_cls.__tablename__


async def collect_lib_quality(db: AsyncSession, source_video_id: str) -> list[dict[str, Any]]:
    """收集某视频下六张积木表的全部行（按 source_video_id 隔离）。"""
    rows: list[dict[str, Any]] = []
    for cls, label in _LIB_MODELS:
        stmt = (
            select(cls.id, cls.quality_score, cls.review_status)
            .where(cls.source_video_id == source_video_id)
        )
        res = await db.execute(stmt)
        for rid, q, rs in res.all():
            rows.append(
                {
                    "table": label,
                    "id": str(rid),
                    "quality_score": q,
                    "review_status": rs,
                }
            )
    return rows


async def collect_job_quality(
    db: AsyncSession, job: M.BreakdownJob
) -> dict[str, Any]:
    """按拆解任务聚合质量：script + 六表 + validation 摘要。

    job.evidence 由 pipeline 写入 validation 统计（修复4 已接线），此处直接透出。
    """
    result: dict[str, Any] = {
        "job_id": str(job.id),
        "video_id": str(job.video_id) if job.video_id else None,
        "status": job.status,
    }
    # script 侧
    if job.script_id:
        script = await db.get(M.ScriptScript, job.script_id)
        if script is not None:
            result["script"] = {
                "id": str(script.id),
                "title": script.title,
                "category": script.category,
                "status": script.status,
                "quality_score": script.quality_score,
            }
    # 积木侧（lib_* 按 source_video_id 隔离，String 格式）
    video_id_str = str(job.video_id) if job.video_id else ""
    lib_rows = await collect_lib_quality(db, video_id_str)
    result["lib"] = summarize_lib_rows(lib_rows)
    # validation 统计（evidence 透出）
    result["evidence"] = job.evidence or None
    # 总体判定（沿用 validate 口径）
    scores = []
    if result.get("script") and result["script"].get("quality_score") is not None:
        scores.append(float(result["script"]["quality_score"]))
    for r in lib_rows:
        if r.get("quality_score") is not None:
            scores.append(float(r["quality_score"]))
    result["overall"] = (
        {
            "score": round(sum(scores) / len(scores), 1),
            "draft": any(s < DRAFT_THRESHOLD for s in scores),
            "checked": len(scores),
        }
        if scores
        else None
    )
    return result


async def quality_overview(db: AsyncSession, limit: int = 20) -> list[dict[str, Any]]:
    """最近拆解任务的质量看板（只读）：job 状态 + script 质量 + 积木 draft 数。"""
    stmt: Select = (
        select(M.BreakdownJob)
        .order_by(M.BreakdownJob.created_at.desc())
        .limit(limit)
    )
    res = await db.execute(stmt)
    jobs = res.scalars().all()
    out: list[dict[str, Any]] = []
    for job in jobs:
        try:
            agg = await collect_job_quality(db, job)
            out.append(
                {
                    "job_id": agg["job_id"],
                    "video_id": agg["video_id"],
                    "status": agg["status"],
                    "script_score": (agg.get("script") or {}).get("quality_score"),
                    "lib_total": agg["lib"]["total"],
                    "lib_draft": agg["lib"]["draft"],
                    "lib_avg": agg["lib"]["avg_score"],
                    "overall": agg.get("overall"),
                }
            )
        except Exception as exc:  # noqa: BLE001 - 看板单条失败不影响整体
            logger.warning("quality_overview 跳过 job %s: %s", job.id, exc)
    return out
