"""元素质控路由：L5 提炼元素的采纳 / 驳回 / 纠错。"""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as M
from app.db import get_session

router = APIRouter()


class ElementReviewBody(BaseModel):
    action: Literal["accept", "reject", "adjust"]
    # adjust 时携带修正字段（只更新非空项）
    patch: dict | None = None
    note: str | None = None


@router.patch("/api/elements/{element_id}/review")
async def review_element(
    element_id: str, body: ElementReviewBody, db: AsyncSession = Depends(get_session)
):
    element = (
        await db.execute(select(M.Element).where(M.Element.id == element_id))
    ).scalar_one_or_none()
    if element is None:
        raise HTTPException(status_code=404, detail="元素不存在")

    status_map = {"accept": "accepted", "reject": "rejected", "adjust": "adjusted"}
    element.status = status_map[body.action]

    if body.action == "adjust" and body.patch:
        for key in ("category", "name", "description", "formula"):
            val = body.patch.get(key)
            if isinstance(val, str) and val.strip():
                setattr(element, key, val.strip())

    await db.commit()
    return {"id": str(element.id), "status": element.status}


@router.get("/api/elements")
async def list_elements(
    status: str | None = None,
    category: str | None = None,
    q: str | None = None,
    analysis_id: str | None = None,
    limit: int = 300,
    db: AsyncSession = Depends(get_session),
):
    """跨片元素库聚合检索：按状态/分类/关键词/来源拆解过滤，未处理元素优先。"""
    conds: list = []
    if status and status != "all":
        conds.append(M.Element.status == status)
    if category and category != "all":
        conds.append(M.Element.category == category)
    if q and q.strip():
        kw = f"%{q.strip()}%"
        conds.append(
            or_(
                M.Element.name.ilike(kw),
                M.Element.description.ilike(kw),
                M.Element.formula.ilike(kw),
            )
        )
    if analysis_id:
        try:
            conds.append(M.Element.analysis_id == UUID(analysis_id))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="analysis_id 非法") from exc

    total = (
        await db.execute(select(func.count()).select_from(M.Element).where(*conds))
    ).scalar()
    status_rows = (
        await db.execute(
            select(M.Element.status, func.count())
            .where(*conds)
            .group_by(M.Element.status)
        )
    ).all()
    status_counts = {s: c for s, c in status_rows}

    # 待处理 draft 最优先 → 已纠错 → 已采纳 → 已驳回，便于集中质控
    order = case(
        (M.Element.status == "draft", 0),
        (M.Element.status == "adjusted", 1),
        (M.Element.status == "accepted", 2),
        (M.Element.status == "rejected", 3),
        else_=4,
    )
    els = (
        (
            await db.execute(
                select(M.Element)
                .where(*conds)
                .order_by(order, M.Element.updated_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )

    # 创作数据回流：统计每个元素被多少个创作项目引用（Creations.core_elements）
    usage_counts: dict[str, int] = {}
    usage_rows = await db.execute(select(M.Creation.core_elements))
    for (arr,) in usage_rows.all():
        if not arr:
            continue
        seen: set[str] = set()
        for item in arr:
            if not isinstance(item, dict):
                continue
            eid = item.get("element_id")
            if eid and eid not in seen:
                seen.add(eid)
                usage_counts[eid] = usage_counts.get(eid, 0) + 1

    a_ids = {str(e.analysis_id) for e in els if e.analysis_id}
    video_by_analysis: dict = {}
    if a_ids:
        rows = await db.execute(
            select(M.Analysis.id, M.Video.id, M.Video.title, M.Video.platform, M.Video.author_name)
            .join(M.Video, M.Video.id == M.Analysis.video_id)
            .where(M.Analysis.id.in_([UUID(x) for x in a_ids]))
        )
        for aid, vid, vt, vp, va in rows.all():
            video_by_analysis[str(aid)] = {
                "id": str(vid),
                "title": vt,
                "platform": vp,
                "author_name": va,
            }

    items = []
    for e in els:
        items.append(
            {
                "id": str(e.id),
                "analysis_id": str(e.analysis_id) if e.analysis_id else None,
                "usage_count": usage_counts.get(str(e.id), 0),
                "category": e.category,
                "name": e.name,
                "description": e.description,
                "formula": e.formula,
                "source_type": e.source_type,
                "confidence": e.confidence,
                "role_view": e.role_view,
                "evidence": e.evidence,
                "status": e.status,
                "tags": e.tags or [],
                "created_at": e.created_at.isoformat() if e.created_at else None,
                "updated_at": e.updated_at.isoformat() if e.updated_at else None,
                "video": video_by_analysis.get(str(e.analysis_id)),
            }
        )
    return {"total": total, "status_counts": status_counts, "items": items}
