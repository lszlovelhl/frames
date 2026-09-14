"""元素质控路由：分层积木库元素的采纳 / 驳回 / 纠错 + AI 组合/变异。

第 7 章清空已 DROP 旧表 ``elements`` / ``element_versions``，元素来源改为三层
分库第三层积木库（lib_*）与 AI 组合产物表（lib_mix_draft）。本路由不再直接触达
ORM，全部读写委托给统一读写层 ``app.services.element_library``：

- 元素 id：``"{table}:{uuid}"`` 复合格式（兼容裸露 uuid）；
- 质控四态：draft / accepted / adjusted / rejected，落在各表 review_status 列；
- AI 组合/变异产物：落 ``lib_mix_draft``（draft 待质控）。
"""

import json
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import chat
from app.db import get_session
from app.services import billing, element_library

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
    """元素质控四态流转：accept / reject / adjust（走三层分库读写层）。"""
    try:
        return await element_library.review_element(
            db, element_id, body.action, body.patch
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/elements")
async def list_elements(
    status: str | None = None,
    category: str | None = None,
    q: str | None = None,
    analysis_id: str | None = None,
    limit: int = 300,
    db: AsyncSession = Depends(get_session),
):
    """跨积木库元素聚合检索：按状态/分类/关键词/来源拆解过滤，未处理元素优先。

    analysis_id 传入 BreakdownJob.id（等价旧的 analysis_id），命中该次拆解来源
    视频的元素；组件内已按来源视频回填 analysis_id 字段。
    """
    data = await element_library.list_elements(
        db, status=status, category=category, q=q, limit=limit
    )
    if analysis_id:
        try:
            jid = UUID(analysis_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="analysis_id 非法") from exc
        items = [it for it in data["items"] if it.get("analysis_id") == str(jid)]
        counts: dict[str, int] = {}
        for it in items:
            st = it.get("status") or "draft"
            counts[st] = counts.get(st, 0) + 1
        return {"total": len(items), "status_counts": counts, "items": items}
    return data


class ElementMixBody(BaseModel):
    mode: Literal["mix", "vary"]
    # mix: 2~6 个元素杂交出 2 个融合元素；vary: 1 个母版元素变异出 3 个方向变体
    element_ids: list[str]
    instruction: str | None = None


def _el_card(e: dict) -> str:
    return (
        f"- [{e['category']}] {e['name']}\n"
        f"  作用：{e['description'] or '—'}\n"
        f"  公式/做法：{e['formula'] or '—'}"
    )


@router.post("/api/elements/mix")
async def mix_elements(
    body: ElementMixBody, db: AsyncSession = Depends(get_session)
):
    """元素变异 / 组合：调 pro 模型产出可直接入库的新元素（lib_mix_draft，待质控）。"""
    if body.mode == "mix":
        if len(body.element_ids) < 2:
            raise HTTPException(status_code=400, detail="组合至少选择 2 个元素")
    else:
        if len(body.element_ids) != 1:
            raise HTTPException(status_code=400, detail="变异请选择 1 个母版元素")

    els = await element_library.load_elements(db, body.element_ids)
    if not els:
        raise HTTPException(status_code=404, detail="源元素不存在")
    missing = [x for x in body.element_ids if x not in {e["id"] for e in els}]
    if missing:
        # 复合 id / 裸露 uuid 均可能命中，按 uid 再兜一次
        ids = {e["id"] for e in els}
        uids = {e["id"].split(":", 1)[-1] for e in els}
        unresolved = [x for x in missing if x not in ids and x.split(":", 1)[-1] not in uids]
        if unresolved:
            raise HTTPException(
                status_code=404, detail=f"部分源元素不存在：{', '.join(unresolved)}"
            )

    acc, points = await billing.precheck(db, "chat")

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
            ref_id=els[0]["id"].split(":", 1)[-1],
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

    tag_marks = [e["name"][:20] for e in els]
    items = await element_library.create_mix_drafts(
        db, gen, mode=body.mode, source_names=tag_marks
    )
    if not items:
        raise HTTPException(status_code=502, detail="AI 未返回可用元素，请重试")

    await billing.consume(
        db, account=acc, action="chat", points=points,
        ref_type="element", ref_id=None,
        note=f"AI 元素组合/变异（源：{'; '.join(tag_marks)}）",
    )
    await db.commit()
    return {"items": items}


@router.get("/api/elements/{element_id}/versions")
async def element_versions(
    element_id: str, db: AsyncSession = Depends(get_session)
):
    """元素演化版本历史。

    注意：旧 ``element_versions`` 表已随第 7 章清空 DROP，三层分库的积木库元素
    不再保留"母版 → 变异/换壳/组合"的版本链（AI 组合产物按 draft 独立落
    ``lib_mix_draft``，来源写入 ``ref_element_ids``）。此接口保留原协议形状，
    元素存在时返回空版本列表，元素不存在时仍返回 404。
    """
    els = await element_library.load_elements(db, [element_id])
    if not els:
        raise HTTPException(status_code=404, detail="元素不存在")
    el = els[0]
    return {
        "element_id": el["id"],
        "element_name": el["name"],
        "items": [],
        "note": "三层分库不保留元素版本链（旧 element_versions 表已清除）；AI 组合产物见 lib_mix_draft",
    }
