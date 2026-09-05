"""创作台路由：对话式创作（元素引用）+ 产物落卡（creations / creation_assets）。"""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as M
from app.ai import chat
from app.db import get_session

router = APIRouter()

SYSTEM_PROMPT = """你是「帧间」的创作搭档，服务对象是短视频编导。

职责：围绕用户的创作意图，把拆解沉淀下来的爆款元素组织成可直接落地拍摄/剪辑的创作方案。可用时会注入「元素卡片」，每条卡片含分类、名称、描述、配方（可复述步骤）。

工作方式：
- 先判断用户意图属于哪个创作阶段：选题构思 / 脚本撰写 / 分镜与拍摄 / 标题与文案 / 修改迭代。
- 需要时主动组合多条元素，并说明你选了哪些、为什么（观察与推断分开讲，避免把猜测当事实）。
- 如果用户意图与注入元素无关，不要强行套用；如果用户直接让改稿，聚焦差异点修改。
- 输出正文尽量结构化、可执行：可用 Markdown 标题/列表/表格组织，涉及视频结构时标注大致时长与时间点；不要输出与创作无关的元信息（不要解释你是谁、不要写“以下是…”之类的引导语）。
"""


class ChatMsg(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str


class CreationChatReq(BaseModel):
    messages: list[ChatMsg]
    element_ids: list[str] = []
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
    # 可选：指定基线版本（某个历史 script_asset 的 id）；缺省则用最新版本
    base_asset_id: str | None = None


class VersionSaveReq(BaseModel):
    content: str
    title: str | None = None
    element_ids: list[str] = []
    parent_asset_id: str | None = None


def _element_cards(elements: list[M.Element]) -> str:
    lines = []
    for e in elements:
        head = f"[{e.category}] {e.name}（{e.status}）"
        parts = [head]
        if e.description:
            parts.append(e.description)
        if e.formula:
            parts.append(f"配方：{e.formula}")
        lines.append("\n".join(parts))
    return "\n\n---\n\n".join(lines) if lines else ""


async def _load_elements(db: AsyncSession, ids: list[str]) -> list[M.Element]:
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
            await db.execute(select(M.Element).where(M.Element.id.in_(uuids)))
        ).scalars()
    )


@router.post("/api/creations/chat")
async def creation_chat(req: CreationChatReq, db: AsyncSession = Depends(get_session)):
    """对话式创作：注入元素卡片上下文，调用 AI 返回创作建议/脚本。"""
    elements = await _load_elements(db, req.element_ids)

    extra = []
    if req.platform:
        extra.append(f"目标平台：{req.platform}")
    if req.intent:
        extra.append(f"创作意图：{req.intent}")
    if elements:
        extra.append("可用的爆款元素卡片如下：\n" + _element_cards(elements))

    sys_msg = SYSTEM_PROMPT
    if extra:
        sys_msg += "\n\n【本次创作上下文】\n" + "\n".join(extra)

    msgs = [{"role": "system", "content": sys_msg}]
    msgs += [m.model_dump() for m in req.messages[-20:]]

    try:
        result = await chat(messages=msgs, model="pro", max_tokens=4096)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"AI 网关调用失败: {exc}") from exc

    return {
        "reply": result["reply"],
        "model": result.get("model"),
        "usage": result.get("usage"),
        "used_elements": [
            {"id": str(e.id), "category": e.category, "name": e.name, "status": e.status}
            for e in elements
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

    sys_msg = SYSTEM_PROMPT
    if extra:
        sys_msg += "\n\n【本次创作上下文】\n" + "\n".join(extra)

    msgs = [{"role": "system", "content": sys_msg}]
    msgs += [m.model_dump() for m in req.messages[-20:]]

    try:
        result = await chat(messages=msgs, model="pro", max_tokens=4096)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"AI 网关调用失败: {exc}") from exc

    return {
        "reply": result["reply"],
        "model": result.get("model"),
        "usage": result.get("usage"),
        "current_version": version,
        "base_asset_id": base_asset_id,
        "used_elements": [
            {"id": str(e.id), "category": e.category, "name": e.name, "status": e.status}
            for e in elements
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
