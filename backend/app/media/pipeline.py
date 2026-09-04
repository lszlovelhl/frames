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

from app.media import audiofeat, downloader, frames as frame_mod, platform as platform_mod
from app.media.transcribe import transcribe
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
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
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

    manifest: dict[str, Any] = {
        "platform": platform,
        "work_dir": str(work_dir),
        "video_path": video_path,
        "meta": meta,
        "duration_ms": duration_ms,
        "warnings": warnings,
    }

    # 2. ASR 全量文案
    transcript: dict = {"text": "", "segments": []}
    await _emit(progress, "transcribe", "running")
    try:
        transcript = await transcribe(video_path, work_dir)
        manifest["transcript"] = transcript
        await _emit(progress, "transcribe", "done", f"{len(transcript.get('segments', []))} 段")
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"语音转写失败：{exc}")
        manifest["transcript"] = transcript
        await _emit(progress, "transcribe", "failed", str(exc)[:200])
        logger.exception("ASR 失败")

    # 3. 按段抽帧
    await _emit(progress, "frames", "running")
    try:
        frames = await frame_mod.sample_frames(
            video_path, work_dir, transcript.get("segments"), duration_hint=duration_ms / 1000
        )
        manifest["frames"] = frames
        await _emit(progress, "frames", "done", f"{len(frames)} 帧")
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
                ["ffmpeg", "-y", "-i", video_path, "-vn", "-ac", "1",
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
    json_path = work_dir / "manifest.json"
    try:
        json_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1, default=str))
    except Exception:  # noqa: BLE001
        pass
    return manifest
