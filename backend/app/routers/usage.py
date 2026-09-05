"""AI 用量与计费可视化路由：累计/今日统计 + 最近调用流（实时监测数据源）。"""

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as M
from app.db import get_session

router = APIRouter()

# 页面展示统一按中国时区（本机演示多按本地，仍以 UTC 落库）
LOCAL_TZ = ZoneInfo("Asia/Shanghai")


def _local_day_range_utc() -> tuple[datetime, datetime]:
    """返回今天（Asia/Shanghai）对应的 UTC [起, 止)。"""
    now_local = datetime.now(LOCAL_TZ)
    day_start_local = datetime.combine(now_local.date(), time.min, tzinfo=LOCAL_TZ)
    start_utc = day_start_local.astimezone(timezone.utc)
    end_utc = start_utc + timedelta(days=1)
    return start_utc, end_utc


@router.get("/api/ai/usage/summary")
async def usage_summary(db: AsyncSession = Depends(get_session)):
    """累计 + 今日用量（调用次数 / token / 估算成本），并按档位拆分。"""
    day_start, day_end = _local_day_range_utc()

    async def _agg(conds: list):
        return (
            await db.execute(
                select(
                    func.count().label("calls"),
                    func.coalesce(func.sum(M.AiUsageLog.prompt_tokens), 0).label("prompt"),
                    func.coalesce(func.sum(M.AiUsageLog.completion_tokens), 0).label("completion"),
                    func.coalesce(func.sum(M.AiUsageLog.total_tokens), 0).label("tokens"),
                    func.coalesce(func.sum(M.AiUsageLog.cost_cny), 0).label("cost"),
                ).where(*conds)
            )
        ).one()

    def _fmt(row) -> dict:
        return {
            "calls": int(row.calls),
            "prompt_tokens": int(row.prompt),
            "completion_tokens": int(row.completion),
            "total_tokens": int(row.tokens),
            "cost_cny": round(float(row.cost), 6),
        }

    total = await _agg([M.AiUsageLog.ok.is_(True)])
    today = await _agg([M.AiUsageLog.ok.is_(True), M.AiUsageLog.created_at >= day_start, M.AiUsageLog.created_at < day_end])

    rows = (
        await db.execute(
            select(
                M.AiUsageLog.alias,
                func.count(),
                func.coalesce(func.sum(M.AiUsageLog.prompt_tokens), 0),
                func.coalesce(func.sum(M.AiUsageLog.completion_tokens), 0),
                func.coalesce(func.sum(M.AiUsageLog.total_tokens), 0),
                func.coalesce(func.sum(M.AiUsageLog.cost_cny), 0),
            )
            .where(M.AiUsageLog.ok.is_(True))
            .group_by(M.AiUsageLog.alias)
            .order_by(M.AiUsageLog.alias)
        )
    ).all()
    by_alias = [
        {
            "alias": alias,
            "calls": int(c),
            "prompt_tokens": int(p),
            "completion_tokens": int(ct),
            "total_tokens": int(t),
            "cost_cny": round(float(cost), 6),
        }
        for alias, c, p, ct, t, cost in rows
    ]

    return {"total": total, "today": today, "by_alias": by_alias}


@router.get("/api/ai/usage/recent")
async def usage_recent(
    limit: int = 30, db: AsyncSession = Depends(get_session)
):
    """最近调用流水（倒序），供实时监测页轮询。"""
    limit = max(1, min(limit, 200))
    rows = (
        (
            await db.execute(
                select(M.AiUsageLog)
                .order_by(M.AiUsageLog.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [
            {
                "id": str(r.id),
                "scene": r.scene,
                "alias": r.alias,
                "model": r.model,
                "prompt_tokens": r.prompt_tokens,
                "completion_tokens": r.completion_tokens,
                "total_tokens": r.total_tokens,
                "cost_cny": r.cost_cny,
                "ok": r.ok,
                "error": r.error,
                "ref_type": r.ref_type,
                "ref_id": str(r.ref_id) if r.ref_id else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    }
