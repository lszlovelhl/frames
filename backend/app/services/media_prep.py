"""素材采集编排（供拆解路由与后台批量任务共用）。

从 routers/videos.py 抽出：单条同步拆解与后台批量拆解都需要「补齐素材」这一步，
抽到 service 层避免两处逻辑漂移。

进度回调 `progress(stage, status, detail)` 与 media/pipeline.run_pipeline 的 ProgressCb
同签名，调用方可据此把进度落进 analyses.meta["progress"]。
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app import models as M
from app.core.config import MEDIA_DIR
from app.media.frames import FRAMES_POLICY_VERSION
from app.media.pipeline import run_pipeline
from app.media.transcribe import ASR_PIPELINE_VERSION, segments_to_timestamped_text

logger = logging.getLogger(__name__)

ProgressCb = Callable[[str, str, str], Awaitable[None]]


def work_dir_for(url: str) -> Path:
    digest = hashlib.md5(url.encode()).hexdigest()[:16]
    return MEDIA_DIR / f"url_{digest}"


def raw_of(video: M.Video) -> dict:
    return dict(video.raw_files or {})


def normalize_subtitle_text(manifest: dict) -> str:
    """统一 manifest 的逐句文案：``subtitle_text`` 带 [起-止] 时间戳。

    - 新采集：直接用 pipeline 写入的 ``transcript.timestamped_text``；
    - 复用老素材（无该字段）：由 ``segments`` 现算，保证升级后老链接也能带上时间锚点；
    - 无分段（纯 OCR 兜底）时退回纯文本，不伪造时间戳。
    """
    transcript = manifest.get("transcript") or {}
    plain = transcript.get("text") or ""
    ts_text = transcript.get("timestamped_text") or segments_to_timestamped_text(
        transcript.get("segments") or []
    )
    if ts_text:
        transcript["timestamped_text"] = ts_text
    manifest["transcript"] = transcript
    manifest["subtitle_text"] = ts_text or plain
    manifest["subtitle_text_plain"] = plain
    return manifest["subtitle_text"]


def subtitle_for_breakdown(manifest: dict) -> str:
    """拆解用文案：逐句带 [起-止] 时间戳（L3 分段的时间锚点），OCR 兜底时退回纯文本。"""
    transcript = manifest.get("transcript") or {}
    return (
        transcript.get("timestamped_text")
        or manifest.get("subtitle_text")
        or transcript.get("text")
        or ""
    )


def media_spec_stale(raw: dict) -> str:
    """素材规格自检：抽帧策略 / ASR 管线版本落后则返回原因（空串=可复用）。

    只看已有素材：帧或转写缺失由调用方另判。老素材没写版本号（None）一律视为过期，
    因此「短视频密集关键帧」「简体转写」这类策略升级对旧链接同样会强制重采集。
    """
    plan = raw.get("frame_plan") or {}
    if plan.get("policy_version") != FRAMES_POLICY_VERSION:
        return f"抽帧策略 v{plan.get('policy_version')} → v{FRAMES_POLICY_VERSION}"
    transcript = raw.get("transcript") or {}
    if transcript.get("pipeline_version") != ASR_PIPELINE_VERSION:
        return (
            f"ASR 管线 v{transcript.get('pipeline_version')} → v{ASR_PIPELINE_VERSION}"
        )
    return ""


async def download_if_needed(
    video: M.Video,
    db: AsyncSession,
    progress: ProgressCb | None = None,
) -> str:
    """确保本地有可用的原片文件；批量任务在后台线程里执行下载建档。

    单条同步路径的建档在路由里已完成，此处是幂等兜底。
    """
    from app.media import platform as platform_mod
    from app.media.cleanup import POLICY_AUTO
    from app.media.downloader import download

    raw = raw_of(video)
    video_path = raw.get("video_path")
    if video_path and Path(video_path).exists():
        return video_path

    work_dir = Path(raw.get("work_dir") or work_dir_for(video.url))
    platform = video.platform or platform_mod.detect_platform(video.url)
    if progress:
        await progress("download", "running", "下载视频并读取元数据")
    dl = await download(video.url, work_dir, platform=platform)
    meta = dl["meta"]

    video.platform = platform
    merged = dict(raw)
    merged.update(
        {
            "video_path": dl["video_path"],
            "work_dir": str(work_dir),
            "meta": meta,
            "media_status": "downloaded",
            "media_policy": raw.get("media_policy") or POLICY_AUTO,
        }
    )
    video.raw_files = merged
    if not video.title or video.title in ("未命名视频", "待建档"):
        video.title = meta.get("title") or video.title
    if not video.author_name:
        video.author_name = meta.get("uploader")
    if not video.cover_url:
        video.cover_url = meta.get("thumbnail")
    if not video.duration_ms:
        video.duration_ms = int((meta.get("duration") or 0) * 1000) or None
    if not video.tags:
        video.tags = []
    await db.commit()
    if progress:
        await progress("download", "done", video.title or "")
    return dl["video_path"]


async def ensure_media(
    video: M.Video,
    db: AsyncSession,
    with_bgm: bool,
    progress: ProgressCb | None = None,
) -> dict:
    """返回素材 manifest；缺失的采集环节（转写/抽帧/视觉/声学/BGM）在此补齐。"""
    raw = raw_of(video)
    manifest = raw  # raw_files 本身即 manifest 结构
    normalize_subtitle_text(manifest)  # 复用路径也要保证逐句文本带时间锚点
    has_transcript = bool((raw.get("transcript") or {}).get("text"))
    has_frames = bool(raw.get("frames"))
    stale_reason = media_spec_stale(raw)
    if (
        has_transcript
        and has_frames
        and raw.get("media_status") in ("analyzed", "ready")
        and not stale_reason
    ):
        if progress:
            await progress("reuse", "done", "复用已有素材")
        return manifest
    if stale_reason and has_transcript and has_frames:
        # 历史素材的帧策略/ASR 管线版本落后：不复用，落到下面整链路重采集
        # （ffmpeg 重抽帧 + whisper 重转写），否则升级对老链接永远不生效
        logger.info("素材版本过期（%s），强制重新采集", stale_reason)
        if progress:
            await progress("reuse", "running", f"素材版本更新（{stale_reason}），强制重采集")

    try:
        video_path = await download_if_needed(video, db, progress=progress)
    except Exception as exc:  # noqa: BLE001 下载失败信息对用户更友好
        raise RuntimeError(f"视频下载失败：{exc}") from exc
    raw = raw_of(video)
    work_dir = Path(raw.get("work_dir") or work_dir_for(video.url))
    if not video_path or not Path(video_path).exists():
        raise RuntimeError(
            "视频文件缺失（可能已被清理），请删除该素材后重新粘贴链接建档"
        )

    manifest = await run_pipeline(
        video.url,
        work_dir,
        platform_hint=video.platform,
        with_bgm=with_bgm,
        existing_video=video_path,
        existing_meta=raw.get("meta") or {},
        progress=progress,
    )
    transcript = manifest.get("transcript") or {}
    text = transcript.get("text") or ""
    manifest["subtitle_text"] = normalize_subtitle_text(manifest)
    # 记录本次是否因版本升级强制重采集（排障用：对接「为什么又跑了一遍」）
    manifest["recollect_reason"] = stale_reason or None

    video.raw_files = manifest
    video.raw_files["media_status"] = "ready"
    # 文案来源落库：ASR 为主，纯字幕卡点/无人声时以画面文字(OCR)兜底，
    # 两种都拿到则标注为互补，便于前端展示与排查（不再是含糊的空串）。
    ts = manifest.get("text_source") or {}
    mode = ts.get("mode")
    video.subtitle_source = {
        "asr": "语音转写",
        "asr+ocr": "语音转写+画面文字(OCR)",
        "ocr_only": "画面文字(OCR)",
        "none": "未获得文案",
    }.get(mode, video.subtitle_source or ("语音转写" if text else "未获得文案"))
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


def media_brief_of(manifest: dict) -> str:
    from app.services.analysis import build_media_brief

    return build_media_brief(manifest)


# 采集阶段 → 中文进度文案（前端常驻进度条直接展示）
STAGE_LABELS: dict[str, str] = {
    "reuse": "复用已有素材",
    "platform": "识别平台",
    "download": "下载视频并读取元数据",
    "transcribe": "语音转文字（全量 ASR）",
    "frames": "按内容分段抽帧",
    "vision": "视觉模型逐段画面理解",
    "audio": "声学分析（响度 / BPM）",
    "bgm": "BGM 分离",
}
