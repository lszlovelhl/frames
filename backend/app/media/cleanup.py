"""素材原片清理服务（A+B 方案）

背景：素材采集（pipeline）把整支原片下载到 backend/data/media/url_xxx/。
拆解完成后原片只用于详情页播放器，数百 MB ~ 数 GB 长期占用磁盘。
本服务在确认「拆解已 done + 分析产物已落库」后，按策略执行：

  auto        拆解完成删原片；先抽 128k aac 音轨 audio_track.m4a（供将来 demucs）。
              ※ 超长截断视频：删除完整大原片，video_truncated.mp4 以预览角色保留。
  keep_preview 删原片前压一条 720p / ~2Mbps 的 preview.mp4 作本地播放源再删。
  keep_full   不删（保留完整原片）。

删除对象严格限定：work_dir 顶层完整原片（保留角色 video_truncated/preview/audio_track 永不删；
当前 video_path 若属于完整原片也一并删，除非改指保留角色）+ audio_16k.wav。
frames/、stems/、manifest.json、关键帧图片一律不动。
失败只记 warning，不阻塞（除非前置校验不满足）。
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as M

from app.media import ffbin

logger = logging.getLogger(__name__)

POLICY_AUTO = "auto"
POLICY_KEEP_PREVIEW = "keep_preview"
POLICY_KEEP_FULL = "keep_full"
VALID_POLICIES = (POLICY_AUTO, POLICY_KEEP_PREVIEW, POLICY_KEEP_FULL)

# 保留角色文件名：无论策略如何都不删
KEEPED_BASENAMES = {"video_truncated.mp4", "preview.mp4", "audio_track.m4a"}
VIDEO_EXTS = {
    ".mp4", ".mov", ".mkv", ".flv", ".webm", ".m4v", ".avi", ".ts", ".wmv",
}

AAC_BITRATE = "128k"          # 音轨
PREVIEW_VBITRATE = "2000k"    # ~2Mbps 视频
PREVIEW_MAXRATE = "2500k"


def _run_ff_sync(args: list[str]) -> None:
    subprocess.run(args, check=True, capture_output=True, text=True)


async def _run_ff(args: list[str]) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _run_ff_sync, args)


async def _extract_audio(inp: Path, out: Path) -> None:
    """抽 m4a 音轨：128k aac（保留声道，供将来 demucs 分离）。"""
    await _run_ff(
        [ffbin.ffmpeg_bin(), "-y", "-loglevel", "error", "-i", str(inp),
         "-vn", "-c:a", "aac", "-b:a", AAC_BITRATE, "-movflags", "+faststart",
         str(out)],
    )


async def _make_preview(inp: Path, out: Path) -> None:
    """压 720p / ~2Mbps 预览片：竖屏视频按宽 720、横屏按高 720 等比缩放。"""
    await _run_ff(
        [ffbin.ffmpeg_bin(), "-y", "-loglevel", "error", "-i", str(inp),
         "-vf", "scale='if(gt(iw,ih),-2,720)':'if(gt(iw,ih),720,-2)'",
         "-c:v", "libx264", "-preset", "veryfast",
         "-b:v", PREVIEW_VBITRATE, "-maxrate", PREVIEW_MAXRATE, "-bufsize", "5000k",
         "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
         str(out)],
    )


def _is_safe_child(work_dir: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(work_dir.resolve())
    except ValueError:
        return False
    return candidate.is_file()


def _collect_originals(raw: dict, work_dir: Path) -> list[Path]:
    """work_dir 顶层视频文件（除保留角色与当前 video_path），即“完整原片”候选。"""
    video_path = raw.get("video_path")
    current_name = Path(video_path).name if video_path else None
    found: list[Path] = []
    for p in sorted(work_dir.iterdir()):
        if not p.is_file():
            continue
        if p.suffix.lower() not in VIDEO_EXTS:
            continue
        if p.name in KEEPED_BASENAMES:
            continue
        if current_name and p.name == current_name:
            continue  # 当前作为采集/播放源的文件由上层决定是否删除
        found.append(p)
    return found


def _norm_policy(raw: dict, policy: str | None) -> str:
    if policy in VALID_POLICIES:
        return policy
    return raw.get("media_policy") if raw.get("media_policy") in VALID_POLICIES else POLICY_AUTO


async def _ensure_analysis_done(db: AsyncSession, video: M.Video) -> tuple[bool, str]:
    """前置校验：最近一次拆解 done 且分析产物已落库（缺一不可）。

    旧 analyses 表已随第 7 章清空 DROP，拆解任务改由 breakdown_jobs 承载。
    """
    latest = (
        await db.execute(
            select(M.BreakdownJob)
            .where(M.BreakdownJob.video_id == video.id)
            .order_by(M.BreakdownJob.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if latest is None:
        return False, "尚无拆解记录，禁止清理原片"
    if latest.status != "done":
        return False, f"最近拆解状态为 {latest.status}（需 done），暂不清理"
    raw = dict(video.raw_files or {})
    has_transcript = bool((raw.get("transcript") or {}).get("text"))
    has_frames = bool(raw.get("frames"))
    if not (has_transcript or has_frames):
        return False, "分析产物（转写/关键帧）未落库，禁止清理原片"
    return True, ""


async def cleanup_video(
    db: AsyncSession,
    video: M.Video,
    policy: str | None = None,
    *,
    auto_trigger: bool = False,
) -> dict:
    """按策略清理单个视频的完整原片。

    返回值统一为摘要 dict（手动接口直接回包；自动触发只取 warnings/result 记日志）。
    前置校验失败返回 skipped_reason，不抛异常（调用方按需 409）。
    """
    raw = dict(video.raw_files or {})
    eff_policy = _norm_policy(raw, policy)
    raw["media_policy"] = eff_policy

    result: dict = {
        "video_id": str(video.id),
        "policy": eff_policy,
        "cleaned": False,
        "deleted": [],
        "retained": [],
        "warnings": [],
        "skipped_reason": "",
        "auto": auto_trigger,
        "blocked": False,  # True=前置校验不满足，禁止清理（手动接口应 409）
    }

    work_dir_raw = raw.get("work_dir")
    if not work_dir_raw:
        result["skipped_reason"] = "无 work_dir（未走本地采集链路）"
        result["blocked"] = True
        return result
    work_dir = Path(work_dir_raw)
    if not work_dir.is_dir():
        result["skipped_reason"] = f"work_dir 不存在：{work_dir}"
        result["blocked"] = True
        return result

    # 已清理过 → 幂等跳过
    if raw.get("cleaned"):
        result["skipped_reason"] = "原片此前已清理（cleaned=true）"
        result["cleaned"] = True
        return result

    ok, why = await _ensure_analysis_done(db, video)
    if not ok:
        result["skipped_reason"] = why
        result["blocked"] = True
        return result

    if eff_policy == POLICY_KEEP_FULL:
        video.raw_files = raw
        await db.commit()
        result["skipped_reason"] = "keep_full：保留完整原片"
        return result

    deleted: list[str] = []
    warnings: list[str] = []
    truncated = bool(raw.get("truncated"))
    video_path = raw.get("video_path")

    def _candidate_sources() -> list[Path]:
        """完整原片候选：work_dir 顶层非保留角色视频 + 当前采集源（若非保留角色）。"""
        cands: list[Path] = list(_collect_originals(raw, work_dir))
        if video_path and Path(video_path).is_file() and Path(video_path).name not in KEEPED_BASENAMES:
            cands.append(Path(video_path))
        return cands

    try:
        # ---- 音频：auto 抽 m4a；keep_preview 直接走视频压流（内含音轨） ----
        if eff_policy == POLICY_AUTO:
            cands = _candidate_sources()
            if cands:
                # 取体积最大的完整原片（截断场景优先全片音轨，而非 15 分钟截断版）
                audio_src = max(cands, key=lambda p: p.stat().st_size)
            elif video_path and Path(video_path).is_file():
                audio_src = Path(video_path)  # 仅剩保留角色（如 video_truncated.mp4）时以它为音源
            else:
                audio_src = None
            if audio_src is not None:
                out = work_dir / "audio_track.m4a"
                try:
                    await _extract_audio(audio_src, out)
                    raw["audio_track_path"] = str(out)
                    result["retained"].append(out.name)
                except Exception as exc:  # noqa: BLE001
                    warnings.append(f"音轨抽取失败：{exc}")
            else:
                warnings.append("未找到可用音源，跳过音轨抽取")
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"音轨抽取异常：{exc}")

    if eff_policy == POLICY_KEEP_PREVIEW:
        # keep_preview：已有截断预览片则直接复用；否则压 preview.mp4（720p/~2M）。
        # 预览不可用 → 只记录 warning，保留原片不删，避免详情页丢失本地播放源。
        trunc_vid = work_dir / "video_truncated.mp4"
        if truncated and trunc_vid.exists():
            raw["preview_path"] = str(trunc_vid)
            result["retained"].append(trunc_vid.name)
        else:
            cands = _candidate_sources()
            if not cands and video_path and Path(video_path).is_file():
                cands = [Path(video_path)]
            src = max(cands, key=lambda p: p.stat().st_size) if cands else None
            if src is not None:
                out = work_dir / "preview.mp4"
                try:
                    await _make_preview(src, out)
                    raw["preview_path"] = str(out)
                    result["retained"].append(out.name)
                except Exception as exc:  # noqa: BLE001
                    warnings.append(f"预览片压制失败，保留原片：{exc}")
                    video.raw_files = raw
                    await db.commit()
                    result["warnings"] = warnings
                    return result
            else:
                warnings.append("未找到可压预览的媒体源")
                video.raw_files = raw
                await db.commit()
                result["warnings"] = warnings
                return result

    # ---- 删文件：完整原片 + audio_16k.wav（保留角色 / 预览压失败均跳过删源） ----
    delete_targets: list[Path] = list(_collect_originals(raw, work_dir))
    if video_path and Path(video_path).is_file() and Path(video_path).name not in KEEPED_BASENAMES:
        delete_targets.append(Path(video_path))

    for tgt in delete_targets:
        if not _is_safe_child(work_dir, tgt):
            warnings.append(f"跳过越界路径（不予删除）：{tgt}")
            continue
        try:
            tgt.unlink()
            deleted.append(tgt.name)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"删除失败 {tgt.name}：{exc}")

    # audio_16k.wav 一律清掉（BPM/响度已入库；如需音频用 audio_track.m4a）
    wav = work_dir / "audio_16k.wav"
    if wav.exists():
        try:
            wav.unlink()
            deleted.append(wav.name)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"删除失败 {wav.name}：{exc}")

    # ---- 更新 raw_files 约定 ----
    if eff_policy == POLICY_KEEP_PREVIEW:
        preview = raw.get("preview_path")
        if preview and Path(preview).is_file():
            raw["video_path"] = preview  # 播放器本地播放源改指 preview.mp4 / video_truncated.mp4
        else:
            raw["video_path"] = None
    elif not truncated:
        raw["video_path"] = None  # auto 无截断版 → 无本地播放源（走前端降级 + 回源）
    # auto + truncated：完整原片已删，video_truncated.mp4 仍是可用本地播放源，video_path 保持/指回截断版
    if eff_policy == POLICY_AUTO and truncated:
        trunc_vid = work_dir / "video_truncated.mp4"
        if trunc_vid.exists():
            raw["video_path"] = str(trunc_vid)

    raw["cleaned"] = True
    raw["cleaned_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    # 保留文件清单（相对 work_dir），供排障/未来回放
    kept = list(result["retained"])
    trunc_vid = work_dir / "video_truncated.mp4"
    if trunc_vid.exists():
        kept.append("video_truncated.mp4")
    raw["retained_files"] = sorted(set(kept))

    video.raw_files = raw
    await db.commit()

    if warnings:
        logger.warning("素材清理部分失败 %s: %s", video.id, warnings)
    result.update(
        cleaned=True,
        deleted=deleted,
        retained=raw.get("retained_files", []),
        warnings=warnings,
    )
    return result
