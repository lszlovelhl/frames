"""多模态素材采集编排：链接 → manifest（下载/转写/抽帧/画面简报/声学特征）

manifest 结构（同时落 video.raw_files）：
{
  platform, work_dir, video_path,
  meta: {title, uploader, thumbnail, duration, view_count, ...},
  duration_ms,
  transcript: {language, text, segments, model},
  frames: [{seq,start_ms,end_ms,time_ms,path,desc,text_overlay,emotion,style}],
  audio: {mean_volume_db, bpm},
  bgm: {ok, bgm_path, vocals_path, reason?},
  warnings: [...]
}
失败不整体中止：核心（视频）必须有；其余逐项降级并记 warning。
"""
import asyncio
import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.media import audiofeat, downloader, ffbin, frames as frame_mod, platform as platform_mod
from app.media.transcribe import (
    ASR_PIPELINE_VERSION,
    proofread_segments,
    segments_to_timestamped_text,
    transcribe,
)
from app.media.vision import describe_frames

logger = logging.getLogger(__name__)

ProgressCb = Callable[[str, str, str], Awaitable[None]]  # (stage, status, detail)

STAGES = ["platform", "download", "transcribe", "frames", "vision", "audio", "bgm"]


async def _emit(cb: ProgressCb | None, stage: str, status: str, detail: str = ""):
    if cb:
        res = cb(stage, status, detail)
        if asyncio.iscoroutine(res):
            await res


async def _ffprobe_duration(video_path: str) -> int:
    out = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: subprocess.run(
            [ffbin.ffprobe_bin(), "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", video_path],
            capture_output=True, text=True, check=True,
        ),
    )
    try:
        return int(float(out.stdout.strip()) * 1000)
    except (ValueError, AttributeError):
        return 0


async def run_pipeline(
    url: str,
    work_dir: Path,
    platform_hint: str = "",
    progress: ProgressCb | None = None,
    with_bgm: bool = True,
    existing_video: str | None = None,
    existing_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    warnings: list[str] = []
    # 前置自检：ffmpeg/ffprobe 缺失时立即给出可操作的安装指引，
    # 而不是在下载完成后抛裸 FileNotFoundError 让整条链路 failed
    ffbin.ensure_binaries()
    platform = platform_hint or platform_mod.detect_platform(url)
    await _emit(progress, "platform", "done", platform)
    work_dir.mkdir(parents=True, exist_ok=True)

    # 1. 下载（已有视频文件则复用）
    await _emit(progress, "download", "running")
    if existing_video:
        video_path = existing_video
        meta = existing_meta or {}
        await _emit(progress, "download", "done", meta.get("title") or "(复用已下载文件)")
    else:
        try:
            dl = await downloader.download(url, work_dir, platform=platform)
            video_path = dl["video_path"]
            meta = dl["meta"]
            await _emit(progress, "download", "done", meta.get("title") or "")
        except downloader.DownloadBlocked as exc:
            raise  # 无法下载则整体失败，交由路由层提示

    duration_ms = await _ffprobe_duration(video_path) or int((meta.get("duration") or 0) * 1000)

    # 超长视频策略：>30 分钟自动截取前 15 分钟用于素材采集（ASR/抽帧/声学），
    # 原始文件保留，避免长视频（直播切片/超长口播）拖垮本地 CPU 转写
    TRUNCATE_LIMIT_MS = 30 * 60 * 1000
    TRUNCATE_KEEP_MS = 15 * 60 * 1000
    truncated = False
    if duration_ms > TRUNCATE_LIMIT_MS:
        short_path = work_dir / "video_truncated.mp4"
        try:
            subprocess.run(
                [ffbin.ffmpeg_bin(), "-y", "-i", video_path, "-t", str(TRUNCATE_KEEP_MS / 1000),
                 "-c", "copy", "-avoid_negative_ts", "make_zero", str(short_path)],
                check=True, capture_output=True,
            )
            truncated = True
            warnings.append(
                f"视频时长 {duration_ms / 60000:.0f} 分钟超过 30 分钟，"
                f"已截取前 {TRUNCATE_KEEP_MS / 60000:.0f} 分钟用于素材采集（原始文件保留）"
            )
            video_path = str(short_path)
        except subprocess.CalledProcessError:
            warnings.append("超长视频截取失败，将按完整时长处理，可能较慢")

    manifest: dict[str, Any] = {
        "platform": platform,
        "work_dir": str(work_dir),
        "video_path": video_path,
        "meta": meta,
        "duration_ms": duration_ms,
        "effective_duration_ms": (TRUNCATE_KEEP_MS if truncated else duration_ms),
        "truncated": truncated,
        "warnings": warnings,
        # 素材规格版本：抽帧策略 / ASR 管线。素材复用前据此比对，
        # 版本落后则强制重采集（见 services/media_prep.media_spec_stale）
        "media_spec": {
            "frames_policy_version": frame_mod.FRAMES_POLICY_VERSION,
            "asr_pipeline_version": ASR_PIPELINE_VERSION,
        },
    }

    # 2. ASR 全量文案
    transcript: dict = {"text": "", "segments": []}
    await _emit(progress, "transcribe", "running")
    try:
        transcript = await transcribe(video_path, work_dir)
        # ASR 错字校对：分批 + 上下文纠错，失败自动回退原文
        if transcript.get("segments"):
            corrected = await proofread_segments(transcript["segments"])
            if any(s.get("proofread") for s in corrected):
                transcript["segments"] = corrected
                transcript["text"] = "\n".join(s["text"] for s in corrected)
                transcript["proofread"] = True
        # 逐句时间戳文本：下游拆解按句落库的时间锚点（[起-止] 句子原文）
        transcript["timestamped_text"] = segments_to_timestamped_text(
            transcript.get("segments") or []
        )
        manifest["transcript"] = transcript
        if transcript.get("degenerate"):
            # 多配置重试后仍是退化结果：如实记警告，不冒充"转写成功"
            warnings.append(
                f"语音转写可能不完整（{transcript['degenerate']}），建议核对文案后再拆解"
            )
        await _emit(
            progress, "transcribe", "done",
            f"{len(transcript.get('segments', []))} 段"
            + (f"（{transcript['decode']}）" if transcript.get("decode") else ""),
        )
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"语音转写失败：{exc}")
        manifest["transcript"] = transcript
        await _emit(progress, "transcribe", "failed", str(exc)[:200])
        logger.exception("ASR 失败")

    # 3. 按段抽帧（<5 分钟走短视频密集档，plan_info 记录档位供自检）
    await _emit(progress, "frames", "running")
    plan_info: dict[str, Any] = {}
    try:
        frames = await frame_mod.sample_frames(
            video_path,
            work_dir,
            transcript.get("segments"),
            duration_hint=duration_ms / 1000,
            plan_info=plan_info,
        )
        manifest["frames"] = frames
        manifest["frame_plan"] = plan_info
        await _emit(
            progress, "frames", "done",
            f"{len(frames)} 帧（{'短视频密集档' if plan_info.get('mode') == 'short' else '常规档'}）",
        )
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"抽帧失败：{exc}")
        manifest["frames"] = []
        await _emit(progress, "frames", "failed", str(exc)[:200])

    # 4. 视觉模型画面简报
    if manifest.get("frames"):
        await _emit(progress, "vision", "running")
        try:
            manifest["frames"] = await describe_frames(manifest["frames"])
            done_cnt = sum(1 for f in manifest["frames"] if f.get("desc"))
            await _emit(progress, "vision", "done", f"{done_cnt}/{len(manifest['frames'])} 帧")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"画面理解失败：{exc}")
            await _emit(progress, "vision", "failed", str(exc)[:200])

    # 5. 声学特征（响度 + BPM）
    await _emit(progress, "audio", "running")
    audio_feats: dict[str, Any] = {}
    try:
        audio_feats["mean_volume_db"] = audiofeat.mean_volume_db(video_path)
    except Exception:  # noqa: BLE001
        audio_feats["mean_volume_db"] = None
    wav = work_dir / "audio_16k.wav"
    if not wav.exists():
        try:
            subprocess.run(
                [ffbin.ffmpeg_bin(), "-y", "-i", video_path, "-vn", "-ac", "1",
                 "-ar", "16000", "-c:a", "pcm_s16le", str(wav)],
                check=True, capture_output=True,
            )
        except subprocess.CalledProcessError:
            wav = None
    if wav and wav.exists():
        audio_feats["bpm"] = audiofeat.estimate_bpm(wav)
    manifest["audio"] = audio_feats
    await _emit(progress, "audio", "done", f"bpm={audio_feats.get('bpm')}")

    # 6. BGM 分离（可选，失败不影响拆解）
    bgm: dict[str, Any] = {"ok": False, "reason": "未执行"}
    if with_bgm:
        await _emit(progress, "bgm", "running")
        bgm = audiofeat.split_bgm(video_path, work_dir / "stems")
        if bgm.get("ok"):
            await _emit(progress, "bgm", "done", str(bgm.get("bgm_path", "")))
        else:
            warnings.append(f"BGM 分离未完成：{bgm.get('reason', '')}")
            await _emit(progress, "bgm", "failed", bgm.get("reason", ""))
    manifest["bgm"] = bgm

    # 落一份 manifest.json 便于检索
    manifest["text_source"] = build_text_source(manifest)
    if manifest["text_source"]["mode"] in ("ocr_only", "none"):
        warnings.append(
            "语音文案缺失（可能是纯 BGM/无人声或 ASR 失败），已回退画面文字（OCR）作为文案来源"
            if manifest["text_source"]["mode"] == "ocr_only"
            else "未取得任何文案来源（ASR 与 OCR 均为空），拆解将依赖画面信息"
        )
    manifest["self_check"] = run_self_check(manifest)

    json_path = work_dir / "manifest.json"
    try:
        json_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1, default=str))
    except Exception:  # noqa: BLE001
        pass
    return manifest


def collect_ocr_text(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按时间顺序汇总画面文字（OCR），供投影/降级使用。"""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for f in sorted(frames, key=lambda x: x.get("time_ms") or 0):
        raw = (f.get("text_overlay") or "").strip()
        if not raw:
            continue
        norm = " ".join(raw.split())
        if len(norm) < 2 or norm in seen:
            continue
        seen.add(norm)
        rows.append({"time_ms": f.get("time_ms") or 0, "text": norm})
    return rows


def build_text_source(manifest: dict[str, Any]) -> dict[str, Any]:
    """判定文案获取方式：语音转写(ASR) / 画面文字(OCR) / 两者兼有。

    拆解与创作的文案应优先用 ASR（时间轴精确）；当 ASR 缺失或明显稀疏
    （纯字幕卡点视频、MV、外语片）时，用 OCR 画面文字补齐，避免"文案为空"。
    """
    transcript = manifest.get("transcript") or {}
    asr_text = (transcript.get("text") or "").strip()
    asr_chars = len(asr_text)
    segs = transcript.get("segments") or []
    effective_s = (manifest.get("effective_duration_ms") or manifest.get("duration_ms") or 0) / 1000.0
    ocr_rows = collect_ocr_text(manifest.get("frames") or [])
    ocr_chars = sum(len(r["text"]) for r in ocr_rows)
    # 语音密度（字/秒）：过低说明基本没口播，需要 OCR 兜底
    density = (asr_chars / effective_s) if effective_s > 1 else 0.0
    sparse = asr_chars < 30 or (effective_s > 20 and density < 0.35)

    if asr_chars and ocr_chars and sparse:
        mode = "asr+ocr"
    elif asr_chars and not sparse:
        mode = "asr_only"
    elif asr_chars:
        mode = "asr"
    elif ocr_chars:
        mode = "ocr_only"
    else:
        mode = "none"

    labels = {
        "asr_only": "语音转写(ASR)",
        "asr": "语音转写(ASR)",
        "asr+ocr": "语音转写(ASR)+画面文字(OCR)补齐",
        "ocr_only": "画面文字(OCR)",
        "none": "无文案来源",
    }
    return {
        "mode": mode,
        "label": labels[mode],
        "asr_chars": asr_chars,
        "asr_segments": len(segs),
        "asr_density_cps": round(density, 3),
        "ocr_frames": len(ocr_rows),
        "ocr_chars": ocr_chars,
        "ocr_rows": ocr_rows[:80],
    }


def run_self_check(manifest: dict[str, Any]) -> dict[str, Any]:
    """采集阶段自检：简繁、文案来源、抽帧档位是否符合预期。"""
    from app.services.zh import has_traditional

    transcript = manifest.get("transcript") or {}
    traditional_hits: list[str] = []
    for seg in transcript.get("segments") or []:
        t = seg.get("text") or ""
        if has_traditional(t):
            traditional_hits.append(t[:60])
        if len(traditional_hits) >= 3:
            break

    plan = manifest.get("frame_plan") or {}
    effective_s = (manifest.get("effective_duration_ms") or 0) / 1000.0
    expect_short = 0 < effective_s < 300
    mode_ok = (plan.get("mode") == "short") if expect_short else (plan.get("mode") == "long")

    return {
        "traditional_in_asr": len(traditional_hits),
        "traditional_samples": traditional_hits,
        "text_source_mode": (manifest.get("text_source") or {}).get("mode"),
        "frame_mode": plan.get("mode"),
        "frame_count": len(manifest.get("frames") or []),
        "frame_mode_ok": mode_ok,
        "duration_s": round(effective_s, 1),
    }
