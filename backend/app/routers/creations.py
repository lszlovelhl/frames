"""创作台路由：对话式创作（元素引用）+ 创作指南（8 章结构化落卡）+ 产物管理。"""

import json
import re
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as M
from app.ai import chat
from app.core.prompts import (
    DEFAULT_CREATION_GUIDE_BRIEF_PROMPT,
    DEFAULT_CREATION_GUIDE_FULL_PROMPT,
    DEFAULT_CREATION_MAIN_PROMPT,
    GUIDE_CHAPTERS,
)
from app.db import get_session
from app.services import billing, element_library
from app.services.llm_json import complete_json
from app.services.prompt_contract import CREATION_OUTPUT_CONTRACT
from app.services.prompt_store import load_active_prompt
from app.services.runtime_config import creation_alias
from app.services.zh import simplify_obj, to_simplified

router = APIRouter()

# 创作指南 8 章 asset_type 顺序（内容 JSON 内嵌 trace 溯源）
GUIDE_ASSET_TYPES = [
    "guide_brief",  # ① 创作任务卡
    "guide_strategy",  # ② 核心策略
    "guide_script",  # ③ 逐段脚本
    "guide_shooting",  # ④ 拍摄执行单
    "guide_editing",  # ⑤ 剪辑执行单
    "guide_publish",  # ⑥ 发布运营
    "guide_checklist",  # ⑦ 自查清单
    "guide_sources",  # ⑧ 参考依据
]

CHAPTER_BY_ASSET = {
    "guide_brief": "创作任务卡",
    "guide_strategy": "核心策略速览",
    "guide_script": "逐段内容脚本",
    "guide_shooting": "拍摄执行单",
    "guide_editing": "剪辑执行单",
    "guide_publish": "发布运营清单",
    "guide_checklist": "避坑与自查",
    "guide_sources": "参考依据",
}


def _extract_json(text: str) -> dict:
    """从模型输出中稳健提取 JSON 对象：优先围栏代码块，其次首尾大括号。"""
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    candidate = fenced.group(1) if fenced else text
    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("模型输出未包含 JSON 对象")
    return json.loads(candidate[start : end + 1])


async def _sys_prompt(db: AsyncSession) -> str:
    """创作主提示词优先读库（creation_main_chat），未 seed 时回退内置默认。"""
    tpl = await load_active_prompt(db, "creation_main_chat")
    return tpl.content if tpl else DEFAULT_CREATION_MAIN_PROMPT


class ChatMsg(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str


class CreationChatReq(BaseModel):
    messages: list[ChatMsg]
    element_ids: list[str] = []
    product_ids: list[str] = []
    platform: str | None = None
    intent: str | None = None


class CreationSaveReq(BaseModel):
    title: str
    content: str
    element_ids: list[str] = []
    platform: str | None = None
    intent: str | None = None


class ContinueChatReq(BaseModel):
    messages: list[ChatMsg]
    element_ids: list[str] = []
    product_ids: list[str] = []
    # 可选：指定基线版本（某个历史 script_asset 的 id）；缺省则用最新版本
    base_asset_id: str | None = None


class VersionSaveReq(BaseModel):
    content: str
    title: str | None = None
    element_ids: list[str] = []
    parent_asset_id: str | None = None


class CreationPublishReq(BaseModel):
    """发布回流登记：上线平台/链接/时间 + 可选手填回流数据。"""

    publish_platform: str
    publish_url: str | None = None
    published_at: datetime | None = None
    stats: dict | None = None


# ---- 创作指南（8 章）----
class GuidePreviewReq(BaseModel):
    """两步走第一步：任务卡 + 核心策略速览（不落库，供用户确认方向）。"""

    messages: list[ChatMsg]
    element_ids: list[str] = []
    product_ids: list[str] = []
    platform: str | None = None
    intent: str | None = None


class GuideChapterIn(BaseModel):
    asset: str  # brief/strategy
    title: str = ""
    text: str = ""
    trace: list[dict] = []


class GuideGenerateReq(BaseModel):
    """两步走第二步：确认速览后补全剩余章节并整份落库。"""

    messages: list[ChatMsg]
    element_ids: list[str] = []
    product_ids: list[str] = []
    platform: str | None = None
    intent: str | None = None
    title: str = ""
    preview: list[GuideChapterIn] = []  # 已确认的①任务卡 + ②策略


def _media_cards(
    elements: list[dict], products: list[M.Product]
) -> tuple[str, list[dict]]:
    """把可用素材展开成可引用清单，供模型在 trace 中按名称引用。

    元素来自三层分库读写层（``element_library.load_elements``），已是前端
    ElementItem 形状的 dict；产品仍为 ORM 对象。

    返回 (素材描述文本, trace 可用列表)。
    """
    lines: list[str] = []
    pool: list[dict] = []
    for e in elements:
        lines.append(
            f"- 元素[{e['category']}]{e['name']}（状态 {e['status']}）：{e['description'] or ''}"
            f"{' 配方:' + e['formula'] if e['formula'] else ''}"
        )
        pool.append(
            {"kind": "element", "name": e["name"], "category": e["category"], "status": e["status"]}
        )
    for p in products:
        lines.append(f"- 产品[{p.industry}]{p.brand} {p.name}：{p.headline or ''}")
        pool.append({"kind": "product", "name": p.name, "brand": p.brand, "industry": p.industry})
    return ("\n".join(lines), pool) if lines else ("", [])


def _system_base(
    platform: str | None, intent: str | None
) -> list[str]:
    extra = []
    if platform:
        extra.append(f"目标平台：{platform}")
    if intent:
        extra.append(f"创作意图：{intent}")
    return extra


async def _guide_brief(
    db: AsyncSession,
    messages: list[ChatMsg],
    elements: list[dict],
    products: list[M.Product],
    platform: str | None,
    intent: str | None,
) -> dict:
    """指南第一步：调 AI 生成任务卡 + 核心策略（JSON 结构）。"""
    tpl = await load_active_prompt(db, "creation_guide_brief")
    sys_msg = tpl.content if tpl else DEFAULT_CREATION_GUIDE_BRIEF_PROMPT
    media_text, pool = _media_cards(elements, products)
    extra = _system_base(platform, intent)
    if media_text:
        extra.append("可引用素材清单（引用其中来源时在对应章节 trace 中按名称标注，不得编造不存在的来源）：\n" + media_text)
    if extra:
        sys_msg += "\n\n【本次创作上下文】\n" + "\n".join(extra)
    sys_msg += "\n\n" + CREATION_OUTPUT_CONTRACT

    msgs = [{"role": "system", "content": sys_msg}]
    msgs += [m.model_dump() for m in messages[-20:]]
    try:
        data, info = await complete_json(
            msgs,
            array_keys=["chapters", "cards", "items"],
            model=creation_alias(),
            max_tokens=8192,
            scene="creation_guide_brief",
            ref_type="creation",
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"AI 网关调用失败: {exc}") from exc
    if not data:
        raise HTTPException(
            status_code=502,
            detail="AI 返回不是合法 JSON，请重试一次。原始回复：\n" + (info.get("raw") or "")[:500],
        )
    data = simplify_obj(data)
    return {
        "data": data,
        "model": info.get("model"),
        "usage": info.get("usage"),
        "pool": pool,
        "quality": {
            "rounds": info.get("rounds"),
            "repaired": info.get("repaired"),
            "truncated": info.get("truncated"),
            "finish_reason": info.get("finish_reason"),
        },
    }


def _norm_trace(raw: list | None, pool: list[dict]) -> list[dict]:
    """把模型返回的 trace 与可用素材池对齐：缺失 kind 时补默认，未在池中的条目丢弃。"""
    if not raw:
        return []
    out: list[dict] = []
    for t in raw:
        if not isinstance(t, dict):
            continue
        name = str(t.get("name") or t.get("label") or "").strip()
        kind = str(t.get("kind") or t.get("type") or "").strip()
        if not name:
            continue
        match = None
        for item in pool:
            if item["kind"] == "product":
                hit = name == item["name"] or name in (item.get("brand", "") + item["name"])
            else:
                hit = name == item["name"] or name in item.get("name", "")
            if hit:
                match = item
                break
        if match:
            entry = {"kind": match["kind"], "name": match["name"]}
            if kind not in ("element", "product"):
                entry["kind"] = match["kind"]
            out.append(entry)
    # 去重
    seen, dedup = set(), []
    for t in out:
        key = (t["kind"], t["name"])
        if key not in seen:
            seen.add(key)
            dedup.append(t)
    return dedup


_INLINE_TRACE_RE = re.compile(r"ⓘ(元素|视频|产品)\s*[:：]\s*([^\s，。；、,;）)\]【】「」]+)")
_INLINE_KIND = {"元素": "element", "视频": "video", "产品": "product"}


def _extract_inline_traces(text: str, pool: list[dict]) -> list[dict]:
    """兜底抽取：正文 ⓘ 行内标注 → 结构化 trace（与素材池对齐、去重）。"""
    raw: list[dict] = []
    for m in _INLINE_TRACE_RE.finditer(text or ""):
        raw.append({"kind": _INLINE_KIND.get(m.group(1), "element"), "name": m.group(2).strip()})
    return _norm_trace(raw, pool)


def _merge_trace(*groups: list[dict]) -> list[dict]:
    """合并多来源 trace 并去重（保持首次出现顺序）。"""
    seen: set[tuple] = set()
    out: list[dict] = []
    for g in groups:
        for t in g or []:
            key = (t.get("kind"), t.get("name"))
            if key not in seen:
                seen.add(key)
                out.append(t)
    return out


def _element_cards(elements: list[dict]) -> str:
    lines = []
    for e in elements:
        head = f"[{e['category']}] {e['name']}（{e['status']}）"
        parts = [head]
        if e["description"]:
            parts.append(e["description"])
        if e["formula"]:
            parts.append(f"配方：{e['formula']}")
        lines.append("\n".join(parts))
    return "\n\n---\n\n".join(lines) if lines else ""


async def _load_elements(db: AsyncSession, ids: list[str]) -> list[dict]:
    """元素装载：改走三层分库读写层（旧 elements 表已 DROP）。

    入参支持 ``"{table}:{uuid}"`` 复合 id 与裸露 uuid，返回前端 ElementItem
    形状的 dict 列表（字段名与旧元素接口一致）。
    """
    if not ids:
        return []
    return await element_library.load_elements(db, ids)


def _product_cards(products: list[M.Product]) -> str:
    """产品卡片：给模型的结构化产品素材（参数 + 卖点 + 种草点）。"""
    lines = []
    for i, p in enumerate(products, 1):
        head = f"{i}. {p.brand} {p.name}"
        if p.series:
            head += f"（{p.series}）"
        if p.price_range:
            head += f" · {p.price_range}"
        head += f" [行业：{p.industry}]"
        parts = [head, f"   一句话种草点：{p.headline}"]
        sps = p.selling_points or []
        if sps:
            parts.append(
                "   卖点：" + "；".join(f"①{s.get('title','')}" for s in sps)
            )
        specs = p.specs or []
        if specs:
            parts.append(
                "   核心参数：" + "、".join(f"{s.get('k','')} {s.get('v','')}" for s in specs[:6])
            )
        lines.append("\n".join(parts))
    return "\n\n".join(lines)


async def _load_products(db: AsyncSession, ids: list[str]) -> list[M.Product]:
    if not ids:
        return []
    uuids: list[UUID] = []
    for x in ids:
        try:
            uuids.append(UUID(x))
        except ValueError:
            continue
    if not uuids:
        return []
    return list(
        (
            await db.execute(
                select(M.Product)
                .where(M.Product.id.in_(uuids), M.Product.status == "active")
            )
        ).scalars()
    )


@router.post("/api/creations/chat")
async def creation_chat(req: CreationChatReq, db: AsyncSession = Depends(get_session)):
    """对话式创作：注入元素卡片 + 产品卡片上下文，调用 AI 返回创作建议/脚本。"""
    elements = await _load_elements(db, req.element_ids)
    products = await _load_products(db, req.product_ids)

    extra = []
    if req.platform:
        extra.append(f"目标平台：{req.platform}")
    if req.intent:
        extra.append(f"创作意图：{req.intent}")
    if elements:
        extra.append("可用的爆款元素卡片如下：\n" + _element_cards(elements))
    if products:
        extra.append(
            "本片需要自然种草/带货的产品（不要生硬硬广，按脚本逻辑融入钩子与卖点）：\n"
            + _product_cards(products)
        )

    sys_msg = await _sys_prompt(db)
    if extra:
        sys_msg += "\n\n【本次创作上下文】\n" + "\n".join(extra)
    sys_msg += "\n\n" + CREATION_OUTPUT_CONTRACT

    msgs = [{"role": "system", "content": sys_msg}]
    msgs += [m.model_dump() for m in req.messages[-20:]]

    acc, points = await billing.precheck(db, "chat")
    try:
        result = await chat(
            messages=msgs,
            model=creation_alias(),
            max_tokens=8192,
            scene="creation_draft",
            ref_type="creation",
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"AI 网关调用失败: {exc}") from exc
    await billing.consume(
        db, account=acc, action="chat", points=points,
        ref_type=None, ref_id=None, note="创作台对话",
    )
    await db.commit()

    return {
        "reply": to_simplified(result["reply"] or ""),
        "model": result.get("model"),
        "usage": result.get("usage"),
        "used_elements": [
            {"id": e["id"], "category": e["category"], "name": e["name"], "status": e["status"]}
            for e in elements
        ],
        "used_products": [
            {"id": str(p.id), "brand": p.brand, "name": p.name, "industry": p.industry}
            for p in products
        ],
    }


@router.post("/api/creations")
async def save_creation(req: CreationSaveReq, db: AsyncSession = Depends(get_session)):
    """把一段创作内容落卡：创建创作项目与脚本资产，引用元素记入 core_elements。"""
    if not req.title.strip() or not req.content.strip():
        raise HTTPException(status_code=400, detail="标题与内容不能为空")

    creation = M.Creation(
        title=req.title.strip(),
        platform=req.platform,
        intent=req.intent,
        core_elements=[
            {"element_id": eid, "role": "", "order": i}
            for i, eid in enumerate(req.element_ids or [])
        ],
        status="idea",
    )
    db.add(creation)
    await db.flush()

    asset = M.CreationAsset(
        creation_id=creation.id,
        asset_type="script",
        role_view="编导",
        content={"text": req.content, "title": req.title},
        ai_generated=True,
    )
    db.add(asset)
    await db.commit()
    await db.refresh(creation)
    return {
        "id": str(creation.id),
        "title": creation.title,
        "platform": creation.platform,
        "intent": creation.intent,
        "status": creation.status,
        "created_at": creation.created_at.isoformat() if creation.created_at else None,
        "assets": 1,
    }


@router.get("/api/creations")
async def list_creations(db: AsyncSession = Depends(get_session)):
    rows = (
        (
            await db.execute(
                select(M.Creation).order_by(M.Creation.updated_at.desc()).limit(200)
            )
        )
        .scalars()
        .all()
    )
    items = []
    for c in rows:
        items.append(
            {
                "id": str(c.id),
                "title": c.title,
                "platform": c.platform,
                "intent": c.intent,
                "status": c.status,
                "core_elements": c.core_elements or [],
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "updated_at": c.updated_at.isoformat() if c.updated_at else None,
            }
        )
    return {"creations": items}


@router.get("/api/creations/{creation_id}")
async def get_creation(creation_id: str, db: AsyncSession = Depends(get_session)):
    try:
        cid = UUID(creation_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="creation_id 非法") from exc
    c = (
        await db.execute(select(M.Creation).where(M.Creation.id == cid))
    ).scalar_one_or_none()
    if c is None:
        raise HTTPException(status_code=404, detail="创作项目不存在")

    assets = list(
        (
            await db.execute(
                select(M.CreationAsset)
                .where(M.CreationAsset.creation_id == cid)
                .order_by(M.CreationAsset.created_at.asc())
            )
        ).scalars()
    )
    versions = _group_versions(assets)
    return {
        "id": str(c.id),
        "title": c.title,
        "platform": c.platform,
        "intent": c.intent,
        "status": c.status,
        "core_elements": c.core_elements or [],
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "assets": [
            {**_asset_view(a), "version": versions.get(str(a.id), 0)}
            for a in assets
        ],
    }


@router.delete("/api/creations/{creation_id}")
async def delete_creation(creation_id: str, db: AsyncSession = Depends(get_session)):
    try:
        cid = UUID(creation_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="creation_id 非法") from exc
    c = (
        await db.execute(select(M.Creation).where(M.Creation.id == cid))
    ).scalar_one_or_none()
    if c is None:
        raise HTTPException(status_code=404, detail="创作项目不存在")
    await db.delete(c)
    await db.commit()
    return {"ok": True}


def _publish_view(p: M.CreationPublish) -> dict:
    return {
        "id": str(p.id),
        "creation_id": str(p.creation_id),
        "publish_platform": p.publish_platform,
        "publish_url": p.publish_url,
        "published_at": p.published_at.isoformat() if p.published_at else None,
        "stats": p.stats or {},
        "stats_fetched_at": p.stats_fetched_at.isoformat() if p.stats_fetched_at else None,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


@router.get("/api/creations/{creation_id}/publishes")
async def list_creation_publishes(
    creation_id: str, db: AsyncSession = Depends(get_session)
):
    """发布回流列表：该创作已登记的线上发布记录（按发布时间倒序）。"""
    try:
        cid = UUID(creation_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="creation_id 非法") from exc
    c = (
        await db.execute(select(M.Creation).where(M.Creation.id == cid))
    ).scalar_one_or_none()
    if c is None:
        raise HTTPException(status_code=404, detail="创作项目不存在")
    rows = (
        (
            await db.execute(
                select(M.CreationPublish)
                .where(M.CreationPublish.creation_id == cid)
                .order_by(M.CreationPublish.published_at.desc().nulls_last(), M.CreationPublish.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return {"creation_id": str(cid), "items": [_publish_view(p) for p in rows]}


@router.post("/api/creations/{creation_id}/publishes")
async def register_creation_publish(
    creation_id: str,
    req: CreationPublishReq,
    db: AsyncSession = Depends(get_session),
):
    """登记一条发布回流：platform/url/published_at 必填项与 stats 可手填 JSON。"""
    platform = (req.publish_platform or "").strip()
    if not platform:
        raise HTTPException(status_code=400, detail="发布平台不能为空")
    try:
        cid = UUID(creation_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="creation_id 非法") from exc
    c = (
        await db.execute(select(M.Creation).where(M.Creation.id == cid))
    ).scalar_one_or_none()
    if c is None:
        raise HTTPException(status_code=404, detail="创作项目不存在")
    now = datetime.now(UTC)
    pub_at = req.published_at
    if pub_at is not None and pub_at.tzinfo is None:
        pub_at = pub_at.replace(tzinfo=UTC)
    p = M.CreationPublish(
        creation_id=cid,
        publish_platform=platform,
        publish_url=(req.publish_url or "").strip() or None,
        published_at=pub_at,
        stats=req.stats or {},
        stats_fetched_at=now if req.stats else None,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return _publish_view(p)


@router.delete("/api/creations/{creation_id}/publishes/{publish_id}")
async def delete_creation_publish(
    creation_id: str, publish_id: str, db: AsyncSession = Depends(get_session)
):
    """删除一条发布回流登记（仅限同一创作下）。"""
    try:
        cid = UUID(creation_id)
        pid = UUID(publish_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="creation_id / publish_id 非法") from exc
    p = (
        await db.execute(
            select(M.CreationPublish).where(
                M.CreationPublish.id == pid,
                M.CreationPublish.creation_id == cid,
            )
        )
    ).scalar_one_or_none()
    if p is None:
        raise HTTPException(status_code=404, detail="发布回流记录不存在")
    await db.delete(p)
    await db.commit()
    return {"ok": True}


async def _load_creation(
    db: AsyncSession, creation_id: str
) -> tuple[M.Creation, list[M.CreationAsset]]:
    """加载创作项目与其资产（按 asset_type 升序时间），404 交给上层。"""
    try:
        cid = UUID(creation_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="creation_id 非法") from exc
    c = (
        await db.execute(select(M.Creation).where(M.Creation.id == cid))
    ).scalar_one_or_none()
    if c is None:
        raise HTTPException(status_code=404, detail="创作项目不存在")
    assets = list(
        (
            await db.execute(
                select(M.CreationAsset)
                .where(M.CreationAsset.creation_id == cid)
                .order_by(M.CreationAsset.created_at.asc())
            )
        ).scalars()
    )
    return c, assets


def _asset_view(a: M.CreationAsset) -> dict:
    return {
        "id": str(a.id),
        "asset_type": a.asset_type,
        "role_view": a.role_view,
        "content": a.content,
        "parent_id": str(a.parent_id) if a.parent_id else None,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


def _group_versions(assets: list[M.CreationAsset]) -> dict[str, int]:
    """asset_type 内按时间升序给版本号，返回 {asset_id: version}。"""
    counters: dict[str, int] = {}
    out: dict[str, int] = {}
    for a in assets:
        n = counters.get(a.asset_type, 0) + 1
        counters[a.asset_type] = n
        out[str(a.id)] = n
    return out


@router.post("/api/creations/{creation_id}/chat")
async def creation_continue_chat(
    creation_id: str, req: ContinueChatReq, db: AsyncSession = Depends(get_session)
):
    """在既有产物上继续对话（改稿/扩写）：注入当前最新版产物与沿用元素，返回新版建议。"""
    c, assets = await _load_creation(db, creation_id)
    script_assets = [a for a in assets if a.asset_type == "script"]

    # 基线版本：默认最新；base_asset_id 指定时校验并选中该历史版本
    if req.base_asset_id:
        latest = next(
            (a for a in script_assets if str(a.id) == req.base_asset_id), None
        )
        if latest is None:
            raise HTTPException(status_code=404, detail="基线版本不存在或不属于该创作项目")
        base_asset_id = str(latest.id)
    else:
        latest = script_assets[-1] if script_assets else None
        base_asset_id = str(latest.id) if latest else None
    version = (
        script_assets.index(latest) + 1 if latest is not None else len(script_assets)
    )

    element_ids: list[str] = list(req.element_ids)
    if not element_ids and c.core_elements:
        element_ids = [e.get("element_id", "") for e in c.core_elements if e.get("element_id")]
    elements = await _load_elements(db, element_ids)
    products = await _load_products(db, req.product_ids)

    extra = [f"当前创作项目：《{c.title}》"]
    if c.platform:
        extra.append(f"目标平台：{c.platform}")
    if c.intent:
        extra.append(f"创作意图：{c.intent}")
    if latest is not None:
        extra.append(
            f"当前为第 {version} 版。用户接下来可能基于这一版做修改/扩写/重写，请聚焦差异点，以用户最新指令为准。\n"
            f"【当前最新版全文】\n{latest.content.get('text', '')}"
        )
    if elements:
        extra.append("可用的爆款元素卡片如下：\n" + _element_cards(elements))
    if products:
        extra.append(
            "本片需要自然种草/带货的产品（不要生硬硬广，按脚本逻辑融入钩子与卖点）：\n"
            + _product_cards(products)
        )

    sys_msg = await _sys_prompt(db)
    if extra:
        sys_msg += "\n\n【本次创作上下文】\n" + "\n".join(extra)
    sys_msg += "\n\n" + CREATION_OUTPUT_CONTRACT

    msgs = [{"role": "system", "content": sys_msg}]
    msgs += [m.model_dump() for m in req.messages[-20:]]

    acc, points = await billing.precheck(db, "chat")
    try:
        result = await chat(messages=msgs, model=creation_alias(), max_tokens=8192, scene="creation_continue")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"AI 网关调用失败: {exc}") from exc
    await billing.consume(
        db, account=acc, action="chat", points=points,
        ref_type=None, ref_id=None, note=f"创作台续写（{c.title}）",
    )
    await db.commit()

    return {
        "reply": to_simplified(result["reply"] or ""),
        "model": result.get("model"),
        "usage": result.get("usage"),
        "current_version": version,
        "base_asset_id": base_asset_id,
        "used_elements": [
            {"id": e["id"], "category": e["category"], "name": e["name"], "status": e["status"]}
            for e in elements
        ],
        "used_products": [
            {"id": str(p.id), "brand": p.brand, "name": p.name, "industry": p.industry}
            for p in products
        ],
    }


@router.post("/api/creations/{creation_id}/versions")
async def save_creation_version(
    creation_id: str, req: VersionSaveReq, db: AsyncSession = Depends(get_session)
):
    """基于当前最新版落一个新版本资产（parent_id 链式继承）。"""
    if not req.content.strip():
        raise HTTPException(status_code=400, detail="内容不能为空")
    c, assets = await _load_creation(db, creation_id)
    script_assets = [a for a in assets if a.asset_type == "script"]

    if req.parent_asset_id:
        try:
            parent_uuid = UUID(req.parent_asset_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="parent_asset_id 非法") from exc
        if parent_uuid not in {a.id for a in script_assets}:
            raise HTTPException(status_code=400, detail="父资产不属于该创作项目")
    else:
        parent_uuid = script_assets[-1].id if script_assets else None

    if req.title and req.title.strip() and req.title.strip() != c.title:
        c.title = req.title.strip()

    if req.element_ids:
        c.core_elements = [
            {"element_id": eid, "role": "", "order": i}
            for i, eid in enumerate(req.element_ids)
        ]
        c.status = "idea"

    asset = M.CreationAsset(
        creation_id=c.id,
        asset_type="script",
        role_view="编导",
        content={"text": req.content, "title": req.title or c.title},
        parent_id=parent_uuid,
        ai_generated=True,
    )
    db.add(asset)
    await db.commit()
    await db.refresh(c)

    # 重新取资产以给出版本号
    _, assets_after = await _load_creation(db, creation_id)
    versions = _group_versions(assets_after)
    view = _asset_view(asset)
    view["version"] = versions.get(str(asset.id), len([a for a in assets_after if a.asset_type == "script"]))
    return {
        "creation_id": str(c.id),
        "title": c.title,
        "platform": c.platform,
        "intent": c.intent,
        "status": c.status,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "asset": view,
    }


# ---- 创作指南：两步走（①速览 → ②确认补全落库）----

@router.post("/api/creations/guide/preview")
async def guide_preview(req: GuidePreviewReq, db: AsyncSession = Depends(get_session)):
    """第一步：先生成创作任务卡 + 核心策略速览（不落库），供用户确认方向。"""
    elements = await _load_elements(db, req.element_ids)
    products = await _load_products(db, req.product_ids)
    out = await _guide_brief(db, req.messages, elements, products, req.platform, req.intent)
    data = out["data"]
    title = str(data.get("title") or "").strip() or "未命名创作指南"
    chapters_raw = data.get("chapters") or []
    chapters = []
    for ch in chapters_raw[:2]:
        if not isinstance(ch, dict):
            continue
        asset = ch.get("asset") or ch.get("type") or ""
        if asset == "task":
            asset = "brief"  # 兼容模型常见的 task 命名
        if asset not in ("brief", "strategy"):
            continue
        text = str(ch.get("text") or "").strip()
        chapters.append(
            {
                "asset": asset,
                "title": str(ch.get("title") or CHAPTER_BY_ASSET[f"guide_{asset}"]).strip(),
                "text": text,
                "trace": _merge_trace(
                    _norm_trace(ch.get("trace"), out["pool"]),
                    _extract_inline_traces(text, out["pool"]),
                ),
            }
        )
    return {
        "title": title,
        "chapters": chapters,
        "model": out.get("model"),
        "usage": out.get("usage"),
    }


@router.post("/api/creations/guide/generate")
async def guide_generate(req: GuideGenerateReq, db: AsyncSession = Depends(get_session)):
    """第二步：确认①任务卡+②策略后，AI 补全③-⑦，后端聚合⑧参考依据，整份落库。

    落库为一个 Creation + 8 个 guide_* 资产（按章节顺序），source 章节由后端
    汇总各章 trace 去重自动生成。
    """
    title = (req.title or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="指南标题不能为空，请先执行速览确认方向")
    brief_map: dict[str, dict] = {}
    for ch in req.preview:
        if ch.asset in ("brief", "strategy") and ch.text.strip():
            brief_map[ch.asset] = {
                "title": ch.title.strip() or CHAPTER_BY_ASSET[f"guide_{ch.asset}"],
                "text": ch.text.strip(),
                "trace": ch.trace if isinstance(ch.trace, list) else [],
            }

    elements = await _load_elements(db, req.element_ids)
    products = await _load_products(db, req.product_ids)

    # --- 计费：生成前预检，成功落库后才扣点 ---
    acc, points = await billing.precheck(db, "guide_generate")

    # --- 调 AI 补全 ③-⑦ ---
    tpl = await load_active_prompt(db, "creation_guide_full")
    sys_msg = tpl.content if tpl else DEFAULT_CREATION_GUIDE_FULL_PROMPT
    media_text, pool = _media_cards(elements, products)
    extra = _system_base(req.platform, req.intent)
    if brief_map:
        extra.append(
            "【已确认方向（请保持一致性，不必重复输出这两章）】\n"
            + "\n\n".join(f"# {v['title']}\n{v['text']}" for v in brief_map.values())
        )
    if media_text:
        extra.append("可引用素材清单（引用其中来源时在对应章节 trace 中按名称标注，不得编造不存在的来源）：\n" + media_text)
    if extra:
        sys_msg += "\n\n【本次创作上下文】\n" + "\n".join(extra)
    sys_msg += "\n\n" + CREATION_OUTPUT_CONTRACT
    msgs = [{"role": "system", "content": sys_msg}]
    msgs += [m.model_dump() for m in req.messages[-20:]]
    try:
        data, info = await complete_json(
            msgs,
            array_keys=["chapters"],
            model=creation_alias(),
            max_tokens=16000,
            scene="creation_guide_full",
            ref_type="creation",
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"AI 网关调用失败: {exc}") from exc
    if not data:
        raise HTTPException(
            status_code=502,
            detail="AI 返回不是合法 JSON，请重试一次。原始回复：\n" + (info.get("raw") or "")[:500],
        )
    data = simplify_obj(data)

    # --- 组装 8 章 ---
    chapters: dict[str, dict] = {
        "brief": {
            "title": brief_map.get("brief", {}).get("title", CHAPTER_BY_ASSET["guide_brief"]),
            "text": brief_map.get("brief", {}).get("text", ""),
            "trace": brief_map.get("brief", {}).get("trace", []),
        },
        "strategy": {
            "title": brief_map.get("strategy", {}).get("title", CHAPTER_BY_ASSET["guide_strategy"]),
            "text": brief_map.get("strategy", {}).get("text", ""),
            "trace": brief_map.get("strategy", {}).get("trace", []),
        },
    }
    if not chapters["brief"]["text"] or not chapters["strategy"]["text"]:
        raise HTTPException(status_code=400, detail="缺少已确认的任务卡/策略，请重新执行速览")
    for raw in (data.get("chapters") or []):
        if not isinstance(raw, dict):
            continue
        asset = raw.get("asset") or raw.get("type") or ""
        if asset not in ("script", "shooting", "editing", "publish", "checklist"):
            continue
        chapters[asset] = {
            "title": str(raw.get("title") or CHAPTER_BY_ASSET[f"guide_{asset}"]).strip(),
            "text": str(raw.get("text") or "").strip(),
            "trace": _merge_trace(
                _norm_trace(raw.get("trace"), pool),
                _extract_inline_traces(str(raw.get("text") or ""), pool),
            ),
        }

    missing = [a for a in ("script", "shooting", "editing", "publish", "checklist") if not chapters.get(a, {}).get("text")]
    if missing:
        raise HTTPException(status_code=502, detail=f"AI 返回缺少章节: {', '.join(missing)}，请重试一次")

    # --- 聚合 ⑧ 参考依据（跨章 trace 去重）---
    seen: set[tuple] = set()
    source_items: list[dict] = []
    for asset, body in chapters.items():
        if asset == "sources":
            continue
        for t in body.get("trace", []):
            key = (t["kind"], t["name"])
            if key in seen:
                continue
            seen.add(key)
            entry = {**t, "chapters": [asset]}
            source_items.append(entry)
    # 补 trace 到前端已确认章节：brief/strategy 无结构化 trace 字段时视为无引用
    chapters["sources"] = {
        "title": CHAPTER_BY_ASSET["guide_sources"],
        "text": "",
        "items": source_items,
    }

    # --- 落库 ---
    creation = M.Creation(
        title=title,
        platform=req.platform,
        intent=req.intent,
        core_elements=[
            {"element_id": eid, "role": "", "order": i}
            for i, eid in enumerate(req.element_ids or [])
        ],
        status="idea",
    )
    db.add(creation)
    await db.flush()

    for asset_type in GUIDE_ASSET_TYPES:
        body = chapters.get(asset_type.split("_", 1)[1], {})
        content = {"title": body.get("title", ""), "text": body.get("text", "")}
        if asset_type == "guide_sources":
            content["items"] = body.get("items", [])
        elif body.get("trace"):
            content["trace"] = body["trace"]
        asset = M.CreationAsset(
            creation_id=creation.id,
            asset_type=asset_type,
            role_view="编导",
            content=content,
            ai_generated=True,
        )
        db.add(asset)
    await db.commit()
    await db.refresh(creation)

    # 指南完整落库成功后才扣点
    await billing.consume(
        db, account=acc, action="guide_generate", points=points,
        ref_type="creation", ref_id=creation.id, note=f"创作指南生成（{title}）",
    )
    await db.commit()

    _, assets_after = await _load_creation(db, str(creation.id))
    versions = _group_versions(assets_after)
    return {
        "id": str(creation.id),
        "title": creation.title,
        "platform": creation.platform,
        "intent": creation.intent,
        "status": creation.status,
        "core_elements": creation.core_elements or [],
        "created_at": creation.created_at.isoformat() if creation.created_at else None,
        "assets": [
            {**_asset_view(a), "version": versions.get(str(a.id), 0)}
            for a in assets_after
        ],
    }
