"""素材与拆解路由：/api/videos、/api/analyses

v2 链路（多模态自动采集）：只填 URL → 自动下载视频 → 拆解前补齐
语音转写全文案 / 分段抽帧画面理解 / BGM 与声学特征 → pro 五层拆解。
"""
import hashlib
import logging
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as M
from app.core.config import MEDIA_DIR
from app.db import get_session
from app.media import platform as platform_mod
from app.media.downloader import DownloadBlocked, download
from app.media.pipeline import run_pipeline
from app.services.analysis import (
    analysis_result,
    build_media_brief,
    create_video_record,
    run_analysis,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["videos"])


# ---------- Schemas ----------

class VideoCreate(BaseModel):
    url: str = ""  # 唯一必填：视频分享链接
    platform: str = ""  # 留空自动识别；手动填可覆盖（bilibili/youtube/douyin/...）
    # 以下字段仅兼容旧手动建档场景，自动抓取时忽略
    platform_video_id: str | None = None
    title: str = ""
    author_name: str | None = None
    cover_url: str | None = None
    subtitle_text: str = ""


class AnalyseReq(BaseModel):
    target_layers: int = Field(default=5, ge=1, le=5)
    with_bgm: bool = True  # 拆解前是否执行 BGM 分离（失败自动降级）
    # 兼容旧版字段，已弃用：model 一律走 pro 专业档
    subtitle_text: str = ""
    model: str | None = None


def _work_dir_for(url: str) -> Path:
    digest = hashlib.md5(url.encode()).hexdigest()[:16]
    return MEDIA_DIR / f"url_{digest}"


def _raw(video: M.Video) -> dict:
    return dict(video.raw_files or {})


def _media_url(path: str | None) -> str | None:
    """将本地媒体绝对路径转为 /media 静态访问 URL（限 MEDIA_DIR 内）。"""
    if not path:
        return None
    try:
        rel = Path(path).resolve().relative_to(MEDIA_DIR.resolve())
    except ValueError:
        return None
    return "/media/" + quote(str(rel))


def _video_public(v: M.Video, extra: dict | None = None) -> dict:
    raw = _raw(v)
    data = {
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
        "media": {
            "video_path": raw.get("video_path"),
            "video_url": _media_url(raw.get("video_path")),
            "has_transcript": bool(raw.get("transcript", {}).get("text")),
            "transcript_segments": len(raw.get("transcript", {}).get("segments", []) or []),
            "frames": len(raw.get("frames", []) or []),
            "bpm": (raw.get("audio") or {}).get("bpm"),
            "bgm_ok": bool((raw.get("bgm") or {}).get("ok")),
            "bgm_path": (raw.get("bgm") or {}).get("bgm_path"),
            "bgm_url": _media_url((raw.get("bgm") or {}).get("bgm_path")),
            "media_status": raw.get("media_status", ""),
        },
        "created_at": v.created_at.isoformat(),
    }
    if extra:
        data.update(extra)
    return data


def _meta_from_raw(raw: dict) -> dict:
    meta = dict(raw.get("meta") or {})
    return meta


# ---------- 建档（自动下载元数据） ----------

@router.post("/api/videos")
async def create_video(req: VideoCreate, db: AsyncSession = Depends(get_session)):
    """只填链接建档：自动识别平台并下载视频、抓取标题/封面/作者等元数据。"""
    if not req.url.strip():
        raise HTTPException(status_code=400, detail="请粘贴视频链接")
    url = req.url.strip()

    # 已存在同链接 → 返回既有记录
    existing = (
        await db.execute(select(M.Video).where(M.Video.url == url).limit(1))
    ).scalar_one_or_none()
    if existing:
        return _video_public(existing, {"existed": True})

    platform = req.platform or platform_mod.detect_platform(url)
    work_dir = _work_dir_for(url)
    try:
        dl = await download(url, work_dir, platform=platform)
    except DownloadBlocked as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    meta = dl["meta"]

    video = M.Video(
        platform=platform,
        url=url,
        title=meta.get("title") or req.title or "未命名视频",
        author_name=meta.get("uploader") or req.author_name,
        author_id=meta.get("uploader_id"),
        cover_url=meta.get("thumbnail") or req.cover_url,
        duration_ms=int((meta.get("duration") or 0) * 1000) or None,
        tags=[],
        stats_snapshot={
            k: meta.get(k)
            for k in ("view_count", "like_count", "comment_count", "publish_date")
            if meta.get(k) is not None
        },
        subtitle_source="",
        raw_files={
            "video_path": dl["video_path"],
            "work_dir": str(work_dir),
            "meta": meta,
            "media_status": "downloaded",
        },
    )
    db.add(video)
    await db.commit()
    await db.refresh(video)
    return _video_public(video, {"existed": False})


# ---------- 拆解（自动补齐多模态素材后执行 pro 五层） ----------

async def _ensure_media(video: M.Video, db: AsyncSession, with_bgm: bool) -> dict:
    """返回素材 manifest；缺失的采集环节（转写/抽帧/视觉/声学/BGM）在此补齐。"""
    raw = _raw(video)
    manifest = raw  # raw_files 本身即 manifest 结构
    has_transcript = bool((raw.get("transcript") or {}).get("text"))
    has_frames = bool(raw.get("frames"))
    if has_transcript and has_frames and raw.get("media_status") in ("analyzed", "ready"):
        return manifest

    work_dir = Path(raw.get("work_dir") or _work_dir_for(video.url))
    video_path = raw.get("video_path")
    if not video_path or not Path(video_path).exists():
        raise HTTPException(
            status_code=400,
            detail="视频文件缺失（可能已被清理），请删除该素材后重新粘贴链接建档",
        )

    manifest = await run_pipeline(
        video.url,
        work_dir,
        platform_hint=video.platform,
        with_bgm=with_bgm,
        existing_video=video_path,
        existing_meta=raw.get("meta") or {},
    )
    transcript = manifest.get("transcript") or {}
    text = transcript.get("text") or ""
    if not text:
        manifest["subtitle_text"] = ""
    else:
        manifest["subtitle_text"] = text

    video.raw_files = manifest
    video.raw_files["media_status"] = "ready"
    video.subtitle_source = "语音转写" if text else video.subtitle_source
    if not video.title or video.title == "未命名视频":
        video.title = (manifest.get("meta") or {}).get("title") or video.title
    if not video.duration_ms and manifest.get("duration_ms"):
        video.duration_ms = manifest["duration_ms"]
    if (manifest.get("meta") or {}).get("uploader") and not video.author_name:
        video.author_name = manifest["meta"]["uploader"]
    if (manifest.get("meta") or {}).get("thumbnail") and not video.cover_url:
        video.cover_url = manifest["meta"]["thumbnail"]
    await db.commit()
    return manifest


@router.post("/api/videos/{video_id}/analyse")
async def start_analysis(
    video_id: str, req: AnalyseReq, db: AsyncSession = Depends(get_session)
):
    """对已建档视频自动补齐多模态素材并执行五层拆解（同步执行）。"""
    video = (
        await db.execute(select(M.Video).where(M.Video.id == video_id))
    ).scalar_one_or_none()
    if video is None:
        raise HTTPException(status_code=404, detail="视频不存在，请先粘贴链接建档")

    try:
        manifest = await _ensure_media(video, db, with_bgm=req.with_bgm)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        logger.exception("素材采集失败")
        raise HTTPException(status_code=502, detail=f"素材采集失败: {exc}") from exc

    transcript = manifest.get("transcript") or {}
    subtitle_text = transcript.get("text") or ""
    if not subtitle_text and not manifest.get("frames"):
        raise HTTPException(
            status_code=400,
            detail="素材不足：语音转写与画面理解均未产出，无法拆解",
        )

    media_brief = build_media_brief(manifest)
    try:
        analysis = await run_analysis(
            db, video, subtitle_text, model="pro",
            target_layers=req.target_layers, media_brief=media_brief,
        )
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        logger.exception("拆解执行失败")
        raise HTTPException(status_code=502, detail=f"拆解执行失败: {exc}") from exc

    result = await analysis_result(db, analysis)
    result["video"] = _video_public(video)
    result["media"] = {
        "transcript_segments": len(transcript.get("segments", []) or []),
        "transcript_chars": len(subtitle_text),
        "frames": len(manifest.get("frames", []) or []),
        "has_bgm": bool((manifest.get("bgm") or {}).get("ok")),
        "bpm": (manifest.get("audio") or {}).get("bpm"),
        "warnings": manifest.get("warnings", []),
    }
    return result


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


@router.get("/api/videos/{video_id}/detail")
async def video_detail_full(video_id: str, db: AsyncSession = Depends(get_session)):
    """详情页全量素材：视频 public + 全量转写 + 关键帧（带静态 URL）+ meta。"""
    video = (
        await db.execute(select(M.Video).where(M.Video.id == video_id))
    ).scalar_one_or_none()
    if video is None:
        raise HTTPException(status_code=404, detail="视频不存在")
    public = _video_public(video)
    raw = video.raw_files or {}
    frames = []
    for f in raw.get("frames") or []:
        out = dict(f)
        out.pop("path", None)
        if f.get("path"):
            out["url"] = _media_url(f["path"])
        frames.append(out)
    public["detail"] = {
        "transcript_text": (raw.get("transcript") or {}).get("text") or "",
        "transcript_segments": (raw.get("transcript") or {}).get("segments") or [],
        "frames": frames,
        "meta": raw.get("meta") or {},
        "audio": raw.get("audio") or {},
    }
    return public


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
