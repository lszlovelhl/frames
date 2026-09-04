"""素材与拆解路由：/api/videos、/api/analyses

MVP 素材链路：手动建档（链接/标题/字幕文本粘贴）→ 触发五层 AI 拆解。
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as M
from app.db import get_session
from app.services.analysis import (
    analysis_result,
    create_video_record,
    run_analysis,
)

router = APIRouter(tags=["videos"])


# ---------- Schemas ----------

class VideoCreate(BaseModel):
    platform: str = Field(default="unknown", description="bilibili/douyin/...")
    platform_video_id: str | None = None
    url: str = ""
    title: str = ""
    author_name: str | None = None
    author_id: str | None = None
    cover_url: str | None = None
    duration_ms: int | None = None
    publish_time: datetime | None = None
    tags: list[str] = []
    stats_snapshot: dict = {}
    category_guess: str | None = None
    subtitle_text: str = ""  # MVP 素材：字幕/旁白全文


class AnalyseReq(BaseModel):
    subtitle_text: str = ""  # 覆盖式素材
    model: str = "flash"  # flash / pro
    target_layers: int = Field(default=5, ge=1, le=5)


def _video_public(v: M.Video) -> dict:
    return {
        "id": str(v.id),
        "platform": v.platform,
        "platform_video_id": v.platform_video_id,
        "url": v.url,
        "title": v.title,
        "author_name": v.author_name,
        "cover_url": v.cover_url,
        "duration_ms": v.duration_ms,
        "publish_time": v.publish_time.isoformat() if v.publish_time else None,
        "tags": v.tags or [],
        "stats_snapshot": v.stats_snapshot or {},
        "subtitle_source": v.subtitle_source,
        "category_guess": v.category_guess,
        "created_at": v.created_at.isoformat(),
    }


# ---------- 视频建档 ----------

@router.post("/api/videos")
async def create_video(req: VideoCreate, db: AsyncSession = Depends(get_session)):
    """建档：已存在则返回既有记录，不重复入库。"""
    try:
        video, existed = await create_video_record(db, req.model_dump(exclude={"subtitle_text"}))
        if req.subtitle_text:
            raw = dict(video.raw_files or {})
            raw["subtitle_text"] = req.subtitle_text
            video.raw_files = raw
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        raise HTTPException(status_code=400, detail=f"建档失败: {exc}") from exc
    data = _video_public(video)
    data["existed"] = existed
    data["has_subtitle"] = bool((video.raw_files or {}).get("subtitle_text"))
    return data


@router.get("/api/videos")
async def list_videos(db: AsyncSession = Depends(get_session)):
    """素材列表（含最近一次拆解状态）。"""
    rows = (
        await db.execute(
            select(M.Video).order_by(M.Video.created_at.desc()).limit(100)
        )
    ).scalars().all()
    result = []
    for v in rows:
        item = _video_public(v)
        latest = (
            await db.execute(
                select(M.Analysis)
                .where(M.Analysis.video_id == v.id)
                .order_by(M.Analysis.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        item["latest_analysis"] = (
            {
                "id": str(latest.id),
                "status": latest.status,
                "current_layer": latest.current_layer,
                "summary": latest.summary,
            }
            if latest
            else None
        )
        result.append(item)
    return {"videos": result}


@router.get("/api/videos/{video_id}")
async def video_detail(video_id: str, db: AsyncSession = Depends(get_session)):
    video = (
        await db.execute(select(M.Video).where(M.Video.id == video_id))
    ).scalar_one_or_none()
    if video is None:
        raise HTTPException(status_code=404, detail="视频不存在")
    return _video_public(video)


# ---------- 拆解 ----------

@router.post("/api/videos/{video_id}/analyse")
async def start_analysis(
    video_id: str, req: AnalyseReq, db: AsyncSession = Depends(get_session)
):
    """对已建档视频执行五层拆解（同步执行，返回完成后的完整结果）。"""
    video = (
        await db.execute(select(M.Video).where(M.Video.id == video_id))
    ).scalar_one_or_none()
    if video is None:
        raise HTTPException(status_code=404, detail="视频不存在，请先建档")
    if not (req.subtitle_text or video.raw_files.get("subtitle_text")):
        raise HTTPException(
            status_code=400,
            detail="缺少素材：请先提供字幕/旁白全文（本版尚不支持自动抓取）",
        )
    subtitle_text = req.subtitle_text or video.raw_files.get("subtitle_text", "")
    try:
        analysis = await run_analysis(
            db, video, subtitle_text, model=req.model, target_layers=req.target_layers
        )
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        raise HTTPException(status_code=502, detail=f"拆解执行失败: {exc}") from exc
    result = await analysis_result(db, analysis)
    result["video"] = _video_public(video)
    return result


@router.get("/api/analyses/{analysis_id}")
async def get_analysis(
    analysis_id: str, db: AsyncSession = Depends(get_session)
):
    analysis = (
        await db.execute(select(M.Analysis).where(M.Analysis.id == analysis_id))
    ).scalar_one_or_none()
    if analysis is None:
        raise HTTPException(status_code=404, detail="拆解不存在")
    return await analysis_result(db, analysis)
