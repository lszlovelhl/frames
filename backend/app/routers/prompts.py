"""提示词管理路由：分层/创作提示词模板的版本管理与热生效。"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as M
from app.db import get_session

router = APIRouter()


class VersionCreateReq(BaseModel):
    content: str = Field(min_length=1)
    name: str | None = None
    activate: bool = True  # True=发布（旧 active 归档）; False=存草稿


class ActivateReq(BaseModel):
    version: int


def _code_summary(tpl: M.PromptTemplate, version_count: int) -> dict:
    return {
        "code": tpl.code,
        "name": tpl.name,
        "layer": tpl.layer,
        "role_scope": tpl.role_scope or [],
        "platform_scope": tpl.platform_scope or [],
        "group_key": tpl.group_key,
        "version_count": version_count,
        "active_version": tpl.version if tpl.status == "active" else None,
        "active_status": tpl.status,
        "updated_at": tpl.updated_at.isoformat() if tpl.updated_at else None,
    }


@router.get("/api/prompts")
async def list_prompts(db: AsyncSession = Depends(get_session)):
    """按 code 分组概览：每组取最新一条作展示，附带总版本数。"""
    rows = (
        (
            await db.execute(
                select(M.PromptTemplate).order_by(M.PromptTemplate.code, M.PromptTemplate.version.desc())
            )
        )
        .scalars()
        .all()
    )
    grouped: dict[str, list[M.PromptTemplate]] = {}
    for t in rows:
        grouped.setdefault(t.code, []).append(t)

    items = []
    for code, versions in grouped.items():
        # 概览以当前线上（active）为准；无 active 时退而取最新一条
        head = next((v for v in versions if v.status == "active"), versions[0])
        items.append(_code_summary(head, len(versions)))
    # 拆分按 layer 排序，创作类（layer 为空）排后
    items.sort(key=lambda x: (x["layer"] is None, x["layer"] or 0, x["code"]))
    return {"items": items}


@router.get("/api/prompts/{code}")
async def prompt_detail(code: str, db: AsyncSession = Depends(get_session)):
    versions = (
        (
            await db.execute(
                select(M.PromptTemplate)
                .where(M.PromptTemplate.code == code)
                .order_by(M.PromptTemplate.version.desc())
            )
        )
        .scalars()
        .all()
    )
    if not versions:
        raise HTTPException(status_code=404, detail=f"提示词 code 不存在: {code}")
    return {
        "code": code,
        "name": versions[0].name,
        "layer": versions[0].layer,
        "versions": [
            {
                "id": str(v.id),
                "version": v.version,
                "status": v.status,
                "name": v.name,
                "content": v.content,
                "updated_at": v.updated_at.isoformat() if v.updated_at else None,
            }
            for v in versions
        ],
    }


@router.post("/api/prompts/{code}/versions")
async def create_version(
    code: str, req: VersionCreateReq, db: AsyncSession = Depends(get_session)
):
    """保存新版本：activate=True 直接发布（同 code 其余 active 归档）；False 仅存草稿。"""
    versions = (
        (
            await db.execute(
                select(M.PromptTemplate)
                .where(M.PromptTemplate.code == code)
                .order_by(M.PromptTemplate.version.desc())
                .limit(1)
            )
        )
        .scalars()
        .all()
    )
    prev = versions[0] if versions else None
    new_version = (prev.version + 1) if prev else 1
    name = req.name or (prev.name if prev else code)

    if req.activate:
        # 同 code 现有 active 全部归档（仅保留一条线上）
        actives = (
            (
                await db.execute(
                    select(M.PromptTemplate).where(
                        M.PromptTemplate.code == code, M.PromptTemplate.status == "active"
                    )
                )
            )
            .scalars()
            .all()
        )
        for a in actives:
            a.status = "archived"

    tpl = M.PromptTemplate(
        code=code,
        name=name,
        layer=prev.layer if prev else None,
        role_scope=prev.role_scope if prev else [],
        platform_scope=prev.platform_scope if prev else [],
        content=req.content,
        version=new_version,
        status="active" if req.activate else "draft",
    )
    db.add(tpl)
    await db.commit()
    await db.refresh(tpl)
    return {
        "id": str(tpl.id),
        "code": code,
        "version": tpl.version,
        "status": tpl.status,
        "name": tpl.name,
    }


@router.post("/api/prompts/{code}/activate")
async def activate_version(
    code: str, req: ActivateReq, db: AsyncSession = Depends(get_session)
):
    """将某历史版本置为线上（同 code 其余 active 归档）。"""
    target = (
        await db.execute(
            select(M.PromptTemplate).where(
                M.PromptTemplate.code == code, M.PromptTemplate.version == req.version
            )
        )
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail=f"版本不存在: {code} v{req.version}")
    if target.status == "archived":
        raise HTTPException(status_code=400, detail="已归档版本不可直接激活，请基于它另存新版本")

    if target.status != "active":
        actives = (
            (
                await db.execute(
                    select(M.PromptTemplate).where(
                        M.PromptTemplate.code == code, M.PromptTemplate.status == "active"
                    )
                )
            )
            .scalars()
            .all()
        )
        for a in actives:
            if a.id != target.id:
                a.status = "archived"
        target.status = "active"
        await db.commit()

    return {"code": code, "version": target.version, "status": target.status}
