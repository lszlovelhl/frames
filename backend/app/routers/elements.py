"""元素质控路由：L5 提炼元素的采纳 / 驳回 / 纠错 + AI 组合/变异。"""

import json
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as M
from app.ai import chat
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


class ElementMixBody(BaseModel):
    mode: Literal["mix", "vary"]
    # mix: 2~6 个元素杂交出 2 个融合元素；vary: 1 个母版元素变异出 3 个方向变体
    element_ids: list[str]
    instruction: str | None = None


def _el_card(e: M.Element) -> str:
    return (
        f"- [{e.category}] {e.name}\n"
        f"  作用：{e.description or '—'}\n"
        f"  公式/做法：{e.formula or '—'}"
    )


def _serialize_element(e: M.Element) -> dict:
    return {
        "id": str(e.id),
        "analysis_id": str(e.analysis_id) if e.analysis_id else None,
        "usage_count": 0,
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
        "video": None,
    }


@router.post("/api/elements/mix")
async def mix_elements(
    body: ElementMixBody, db: AsyncSession = Depends(get_session)
):
    """元素变异 / 组合：调 pro 模型产出可直接入库的新元素（draft，待质控）。"""
    ids: list[UUID] = []
    for raw in body.element_ids:
        try:
            ids.append(UUID(raw))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"非法元素 id: {raw}") from exc

    if body.mode == "mix":
        if len(ids) < 2:
            raise HTTPException(status_code=400, detail="组合至少选择 2 个元素")
    else:
        if len(ids) != 1:
            raise HTTPException(status_code=400, detail="变异请选择 1 个母版元素")

    els = (
        (
            await db.execute(
                select(M.Element).where(M.Element.id.in_(ids))
            )
        )
        .scalars()
        .all()
    )
    if len(els) != len(ids):
        raise HTTPException(status_code=404, detail="部分源元素不存在")
    # 稳定排序：按传入顺序
    order_map = {str(e.id): i for i, e in enumerate(els)}
    els.sort(key=lambda e: order_map[str(e.id)])

    src_text = "\n".join(_el_card(e) for e in els)
    user_parts = [f"源元素：\n{src_text}"]
    if body.instruction and body.instruction.strip():
        user_parts.append(f"额外要求：{body.instruction.strip()}")

    if body.mode == "mix":
        sys = (
            "你是爆款拆解系统的资深编导组合器。将以下多个爆款元素杂交，"
            "产出 2 个新的融合元素：提取不同源元素的钩子/结构/情绪/场景优势融合，"
            "拒绝简单并列拼接，每个融合元素需对编导可直接套用。"
        )
        user_parts.append("请输出 2 个融合元素。")
    else:
        sys = (
            "你是爆款拆解系统的资深编导变异器。以母版元素为基准做创意变异，"
            "产出 3 个差异化变体：分别更换对象/场景/钩子话术/情绪节奏/平台适配，"
            "变体之间拉开差异，每个仍是可单独套用的爆款元素。"
        )
        user_parts.append("请输出 3 个变异体。")

    messages = [
        {"role": "system", "content": sys},
        {
            "role": "user",
            "content": "\n\n".join(user_parts)
            + '\n\n仅输出 JSON：{"elements":[{"category":"","name":"","description":"","formula":""}]}',
        },
    ]

    try:
        result = await chat(
            messages=messages,
            model="pro",
            max_tokens=4096,
            json_mode=True,
            timeout=180,
            scene="element_mix",
            ref_type="element",
            ref_id=str(els[0].id),
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"AI 生成失败: {exc}") from exc

    raw = (result.get("reply") or "").strip()
    data: dict = {}
    if raw:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # 容错：截取首个 {...} 块
            start, end = raw.find("{"), raw.rfind("}")
            if start >= 0 and end > start:
                try:
                    data = json.loads(raw[start : end + 1])
                except json.JSONDecodeError:
                    data = {}

    gen = (data.get("elements") or [])[:6]
    if not gen:
        raise HTTPException(status_code=502, detail="AI 未返回可用元素，请重试")

    tag_marks = [str(e.name)[:20] for e in els]
    created: list[M.Element] = []
    for item in gen:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        category = str(item.get("category") or (els[0].category if body.mode == "vary" else "综合")).strip()[:32] or "综合"
        desc = str(item.get("description") or "").strip()
        formula = str(item.get("formula") or "").strip()
        if not formula and not desc:
            continue
        e = M.Element(
            analysis_id=None,
            category=category,
            name=name[:100],
            description=desc[:1000],
            formula=formula[:2000],
            source_type="combo",
            confidence=0.5,
            role_view="编导",
            status="draft",
            tags=[
                "ai_mix",
                f"mode:{body.mode}",
                f"源自:{'; '.join(tag_marks)}",
            ],
        )
        db.add(e)
        created.append(e)
    await db.commit()
    return {"items": [_serialize_element(e) for e in created]}
