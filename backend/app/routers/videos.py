"""素材与拆解路由：/api/videos、/api/analyses

唯一链路（多模态自动采集 + 三层物理分库）：只填 URL → 自动下载视频 →
拆解前补齐语音转写全文案 / 分段抽帧画面理解 / BGM 与声学特征 →
三层分库拆解（raw_* 原始层 / script_* 拆解层 / lib_* 创作层）。

队列与进度承载：breakdown_jobs（第 7 章已 DROP A 级旧表 analyses /
analysis_layers / segments / analysis_notes / elements / element_versions /
annotations / category_templates，本路由不再引用任何旧表）。
"""

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as M
from app.core.config import MEDIA_DIR
from app.db import get_session
from app.media import platform as platform_mod
from app.media.cleanup import POLICY_AUTO, cleanup_video
from app.media.downloader import DownloadBlocked, download

from app.services import breakdown_jobs
from app.services.analysis import (
    active_job,
    analysis_result,
    create_job,
    latest_job,
    resumable_job,
    run_three_layer_breakdown,
    set_progress,
)
from app.services.categorize import classify_category
from app.services import billing
from app.services.media_prep import ensure_media, subtitle_for_breakdown
from app.services.media_prep import work_dir_for as _work_dir_for
from app.services.runtime_config import breakdown_alias

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


# 批量拆解单次上限（链路为串行队列，防止一次塞入过多把机器占满）
MAX_BATCH_URLS = 20


class BatchAnalyseReq(BaseModel):
    """批量拆解提交：一次粘贴多条链接，后台串行执行。"""

    urls: list[str] = Field(default_factory=list)
    target_layers: int = Field(default=5, ge=1, le=5)
    with_bgm: bool = True


class MediaPolicyReq(BaseModel):
    """素材保留策略：auto / keep_preview / keep_full（默认 auto）。"""
    policy: Literal["auto", "keep_preview", "keep_full"] = "auto"


class VideoCleanupReq(BaseModel):
    """手动触发素材清理；policy 缺省时沿用 raw_files.media_policy（默认 auto）。"""
    policy: Literal["auto", "keep_preview", "keep_full"] | None = None


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
        "author_id": v.author_id,
        "author_avatar": v.author_avatar,
        "author_fans": v.author_fans,
        "author_likes": v.author_likes,
        "cover_url": v.cover_url,
        "duration_ms": v.duration_ms,
        "publish_time": v.publish_time.isoformat() if v.publish_time else None,
        "tags": v.tags or [],
        "stats_snapshot": v.stats_snapshot or {},
        "stats_updated_at": v.stats_updated_at.isoformat()
        if v.stats_updated_at
        else None,
        "subtitle_source": v.subtitle_source,
        "category_guess": v.category_guess,
        "media": {
            "video_path": raw.get("video_path"),
            "video_url": _media_url(raw.get("video_path")),
            # A+B 素材保留策略：auto / keep_preview / keep_full
            "media_policy": raw.get("media_policy") or POLICY_AUTO,
            "cleaned": bool(raw.get("cleaned")),
            "cleaned_at": raw.get("cleaned_at"),
            "audio_track_url": _media_url(raw.get("audio_track_path")),
            "has_transcript": bool(raw.get("transcript", {}).get("text")),
            "transcript_segments": len(raw.get("transcript", {}).get("segments", []) or []),
            "frames": len(raw.get("frames", []) or []),
            "bpm": (raw.get("audio") or {}).get("bpm"),
            "bgm_ok": bool((raw.get("bgm") or {}).get("ok")),
            "bgm_path": (raw.get("bgm") or {}).get("bgm_path"),
            "bgm_url": _media_url((raw.get("bgm") or {}).get("bgm_path")),
            "media_status": raw.get("media_status", ""),
            "truncated": bool(raw.get("truncated")),
            "truncated_to_duration_ms": raw.get("effective_duration_ms") if raw.get("truncated") else None,
        },
        "created_at": v.created_at.isoformat(),
    }
    if extra:
        data.update(extra)
    return data


def _meta_from_raw(raw: dict) -> dict:
    meta = dict(raw.get("meta") or {})
    return meta


def _as_uuid(value: str, *, label: str = "记录") -> UUID:
    """路径参数容错：非法 UUID 直接 404，避免 DB 层抛错变 500。"""
    try:
        return UUID(str(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise HTTPException(status_code=404, detail=f"{label}不存在") from exc


async def _video_or_404(db: AsyncSession, video_id: str) -> M.Video:
    video = (
        await db.execute(select(M.Video).where(M.Video.id == _as_uuid(video_id, label="视频")))
    ).scalar_one_or_none()
    if video is None:
        raise HTTPException(status_code=404, detail="视频不存在")
    return video


async def _job_or_404(db: AsyncSession, analysis_id: str) -> M.BreakdownJob:
    job = (
        await db.execute(
            select(M.BreakdownJob).where(
                M.BreakdownJob.id == _as_uuid(analysis_id, label="拆解")
            )
        )
    ).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="拆解不存在")
    return job


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
            "media_policy": POLICY_AUTO,
        },
    )
    db.add(video)
    await db.commit()
    await db.refresh(video)
    # 建档后自动补内容赛道分类（尽力而为，失败不影响建档返回）
    if not video.category_guess:
        try:
            cat = await classify_category(
                title=video.title,
                author=video.author_name or "",
                tags=video.tags or [],
            )
            if cat:
                video.category_guess = cat
                await db.commit()
                await db.refresh(video)
        except Exception:  # noqa: BLE001
            logger.warning("建档后赛道分类失败，跳过", exc_info=True)
    return _video_public(video, {"existed": False})


# ---------- 拆解（自动补齐多模态素材后执行 pro 五层） ----------

async def _ensure_media(video: M.Video, db: AsyncSession, with_bgm: bool, progress=None) -> dict:
    """返回素材 manifest；缺失的采集环节（转写/抽帧/视觉/声学/BGM）在此补齐。

    实际逻辑在 services/media_prep.ensure_media（后台批量任务复用同一实现）。
    """
    try:
        return await ensure_media(video, db, with_bgm=with_bgm, progress=progress)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/videos/{video_id}/analyse")
async def start_analysis(
    video_id: str, req: AnalyseReq, db: AsyncSession = Depends(get_session)
):
    """对已建档视频自动补齐多模态素材并执行三层分库拆解（同步执行）。"""
    video = await _video_or_404(db, video_id)

    try:
        manifest = await _ensure_media(video, db, with_bgm=req.with_bgm)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        logger.exception("素材采集失败")
        raise HTTPException(status_code=502, detail=f"素材采集失败: {exc}") from exc

    transcript = manifest.get("transcript") or {}
    # 优先逐句带时间戳文本：L3 分段的时间锚点直接取此，避免模型臆造 start_ms/end_ms
    subtitle_text = subtitle_for_breakdown(manifest)
    if not subtitle_text and not manifest.get("frames"):
        raise HTTPException(
            status_code=400,
            detail="素材不足：语音转写与画面理解均未产出，无法拆解",
        )

    # 续跑判定：存在上次未跑完的任务（partial/failed）→ 同一动作延续，免重复扣点
    resume = await resumable_job(db, video.id)
    action = billing.action_for_video(video.duration_ms)
    charge_ctx = None
    if resume is None:
        acc, points = await billing.precheck(db, action)
        charge_ctx = (acc, action, points)

    job = resume or await create_job(db, video.id, model=breakdown_alias())
    job.status = "running"
    job.started_at = job.started_at or datetime.now(UTC)
    await set_progress(db, job, stage="breakdown", message="多模态融合 · 三层拆解开始…", pct=50)
    try:
        result = await run_three_layer_breakdown(
            db, video, job, manifest=manifest, model=breakdown_alias()
        )
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        logger.exception("拆解执行失败")
        raise HTTPException(status_code=502, detail=f"拆解执行失败: {exc}") from exc

    if not result.get("ok"):
        await db.commit()
        raise HTTPException(
            status_code=502,
            detail=f"拆解执行失败: {result.get('error') or '三层链路未产出有效结果'}",
        )

    # 拆解成功后才扣点
    if charge_ctx is not None:
        acc, action, points = charge_ctx
        await billing.consume(
            db, account=acc, action=action, points=points,
            ref_type="video", ref_id=video.id,
            note=f"视频拆解（{action}）",
        )
        await db.commit()

    # A+B 素材策略：三层拆解完成后自动清理大原片（auto/keep_preview）；
    # keep_full / 已清理 / 前置校验不满足会被幂等跳过；任何失败只记 warning，不阻塞响应
    try:
        summary = await cleanup_video(db, video, auto_trigger=True)
        if summary.get("warnings"):
            logger.warning(
                "拆解完成自动清理告警 %s: %s", video.id, summary["warnings"]
            )
    except Exception:  # noqa: BLE001
        logger.exception("拆解完成自动清理失败 %s", video.id)

    result = await analysis_result(db, job)
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


@router.post("/api/videos/batch-analyse")
async def batch_analyse(req: BatchAnalyseReq, db: AsyncSession = Depends(get_session)):
    """批量提交拆解：提交即返回，采集/三层拆解在后台串行执行。

    - 每条链接立刻建/复用视频记录 + 一条 queued 的 breakdown_jobs 行，界面可立即看到"排队中"；
    - 进度写在 breakdown_jobs 的 stage/message/pct，前端轮询 /api/analyses/active 常驻展示；
    - 同一链接已在队列里（queued/running）时跳过，避免重复扣点；
    - 提交时先做一次点数预检，余额不足直接整体拒绝（402）。
    """
    urls: list[str] = []
    for raw_url in req.urls:
        u = (raw_url or "").strip()
        if u and u not in urls:
            urls.append(u)
    if not urls:
        raise HTTPException(status_code=400, detail="请至少粘贴一条视频链接")
    if len(urls) > MAX_BATCH_URLS:
        raise HTTPException(status_code=400, detail=f"单次最多提交 {MAX_BATCH_URLS} 条链接")

    # 点数预检（按最短档位，避免提交后成批失败；实际扣点按各视频时长档位在任务内执行）
    await billing.precheck(db, "breakdown_short")

    queued: list[dict] = []
    skipped: list[dict] = []
    for url in urls:
        video = (
            await db.execute(select(M.Video).where(M.Video.url == url).limit(1))
        ).scalar_one_or_none()
        if video is None:
            video = M.Video(
                platform=platform_mod.detect_platform(url),
                url=url,
                title="待建档",
                tags=[],
                stats_snapshot={},
                subtitle_source="",
                raw_files={"media_policy": POLICY_AUTO},
            )
            db.add(video)
            await db.flush()

        if await active_job(db, video.id) is not None:
            skipped.append({"url": url, "reason": "已在拆解队列中"})
            continue

        # 复用上一次失败/部分失败的任务行：后台在原任务上续跑，省 token
        job = await resumable_job(db, video.id)
        if job is None:
            job = await create_job(db, video.id, model=breakdown_alias())
        job.status = "queued"
        await set_progress(
            db, job, stage="queued", message="已加入拆解队列，等待执行…", pct=0, commit=False
        )
        await db.commit()
        await breakdown_jobs.enqueue(str(job.id))
        queued.append(
            {
                "url": url,
                "video_id": str(video.id),
                "analysis_id": str(job.id),
                "title": video.title,
            }
        )

    return {"queued": queued, "skipped": skipped, "state": await breakdown_jobs.batch_state(db)}


@router.get("/api/analyses/active")
async def active_analyses(db: AsyncSession = Depends(get_session)):
    """拆解进度常驻来源：进行中任务全量 + 最近完成若干条。"""
    return {"items": await breakdown_jobs.batch_state(db)}


@router.get("/api/videos")
async def list_videos(db: AsyncSession = Depends(get_session)):
    """素材列表（含最近一次拆解状态，取 breakdown_jobs 最新一条）。"""
    rows = (
        await db.execute(
            select(M.Video).order_by(M.Video.created_at.desc()).limit(100)
        )
    ).scalars().all()
    result = []
    for v in rows:
        item = _video_public(v)
        latest = await latest_job(db, v.id)
        item["latest_analysis"] = (
            {
                "id": str(latest.id),
                "status": latest.status,
                "current_layer": 5 if latest.status == "done" else 0,
                "summary": {},
            }
            if latest
            else None
        )
        result.append(item)
    return {"videos": result}


@router.get("/api/videos/{video_id}")
async def video_detail(video_id: str, db: AsyncSession = Depends(get_session)):
    video = await _video_or_404(db, video_id)
    return _video_public(video)


@router.get("/api/videos/{video_id}/detail")
async def video_detail_full(video_id: str, db: AsyncSession = Depends(get_session)):
    """详情页全量素材：视频 public + 全量转写 + 关键帧（带静态 URL）+ meta + 互动数据。"""
    video = await _video_or_404(db, video_id)
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

    # 互动数据：自动刷新策略 —— 从未抓取 / 已超过 30 分钟未更新时真实抓取一次；
    # 其余读库（baseline 永不覆盖，diff 相对首轮展示）
    from datetime import UTC, datetime, timedelta

    from app.services.stats_refresh import build_stats_view, refresh_video_public

    stale = True
    if video.stats_updated_at is not None:
        u = video.stats_updated_at
        if u.tzinfo is None:
            u = u.replace(tzinfo=UTC)
        stale = datetime.now(UTC) - u > timedelta(minutes=30)
    if video.platform in ("bilibili", "douyin") and stale:
        view = await refresh_video_public(video, db)
        if view is None:
            # 抓取失败（风控/网络）兜底：读库展示既有数据，不阻塞详情页
            view = await build_stats_view(video, db=db)
    else:
        view = await build_stats_view(video, db=db)
    public["stats"] = view
    return public


@router.get("/api/videos/{video_id}/stats-history")
async def video_stats_history(video_id: str, db: AsyncSession = Depends(get_session)):
    """互动数据历史时序：按 fetched_at 升序返回历次快照全量行（趋势曲线数据源）。

    纯读库不触发抓取；平台不支持的指标（如抖音 view_count）不掺 0。
    """
    video = await _video_or_404(db, video_id)
    from app.services.stats_refresh import build_stats_history

    return await build_stats_history(video, db)


@router.post("/api/videos/{video_id}/stats-refresh")
async def video_stats_refresh(video_id: str, db: AsyncSession = Depends(get_session)):
    """手动刷新视频互动指标与热评（真实平台接口）。"""
    video = await _video_or_404(db, video_id)
    if video.platform not in ("bilibili", "douyin"):
        raise HTTPException(status_code=422, detail="该平台暂未接入互动数据抓取")
    from app.services.stats_refresh import refresh_video_public

    view = await refresh_video_public(video, db)
    if view is None:
        raise HTTPException(status_code=502, detail="平台接口抓取失败，请稍后重试")
    return view


@router.get("/api/analyses/{analysis_id}")
async def get_analysis(
    analysis_id: str, db: AsyncSession = Depends(get_session)
):
    """单次拆解结果（承载表：breakdown_jobs；结果现场聚合自三层分库）。"""
    job = await _job_or_404(db, analysis_id)
    return await analysis_result(db, job)


# ---------- A+B 素材保留策略 ----------


@router.post("/api/videos/{video_id}/media-policy")
async def set_media_policy(
    video_id: str, req: MediaPolicyReq, db: AsyncSession = Depends(get_session)
):
    """设置素材保留策略（不触发清理）：auto / keep_preview / keep_full。"""
    video = await _video_or_404(db, video_id)
    raw = _raw(video)
    raw["media_policy"] = req.policy
    video.raw_files = raw
    await db.commit()
    return _video_public(video)


@router.post("/api/videos/{video_id}/cleanup")
async def manual_cleanup(
    video_id: str, req: VideoCleanupReq, db: AsyncSession = Depends(get_session)
):
    """手动触发素材清理（存量补清用）。

    幂等：已清理 / keep_full / 未到清理条件均返回摘要，由 blocked 表示是否被前置校验拦截。
    前置校验不满足（未拆解、未 done、分析产物未落库、work_dir 缺失）返回 409。
    """
    video = await _video_or_404(db, video_id)
    summary = await cleanup_video(db, video, req.policy)
    if summary.get("blocked"):
        raise HTTPException(status_code=409, detail=summary.get("skipped_reason"))
    return summary
