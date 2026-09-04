"""抽帧：为分段画面理解准备关键帧。

策略：优先按 ASR 段落取时间中点；无分段信息时按时长均匀切 ≤ 16 段。
为省视觉 token，每段只出 1 帧，输出 jpg（宽 ≤1024，质量 82）。
"""
import logging
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MAX_SEGMENTS = 16


def _ffprobe_duration(video_path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", video_path],
        check=True, capture_output=True, text=True,
    )
    return float(out.stdout.strip())


async def sample_frames(
    video_path: str,
    work_dir: Path,
    segments: list[dict] | None = None,
    duration_hint: float | None = None,
) -> list[dict[str, Any]]:
    """返回 [{seq,start_ms,end_ms,time_ms,path}]"""
    frames_dir = work_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    duration = duration_hint or _ffprobe_duration(video_path)
    if not segments:
        step = duration / MAX_SEGMENTS
        segments = [
            {"start": i * step, "end": (i + 1) * step, "text": ""}
            for i in range(min(MAX_SEGMENTS, max(1, int(duration // 10))))
        ]

    # 段过多时按时长聚合：保证 ≤16 段
    if len(segments) > MAX_SEGMENTS:
        seg_step = len(segments) / MAX_SEGMENTS
        merged = []
        for i in range(MAX_SEGMENTS):
            lo = int(i * seg_step)
            hi = max(lo + 1, int((i + 1) * seg_step))
            chunk = segments[lo:hi]
            merged.append(
                {
                    "start": chunk[0]["start"],
                    "end": chunk[-1]["end"],
                    "text": " ".join(s.get("text", "") for s in chunk),
                }
            )
        segments = merged

    frames = []
    import asyncio

    loop = asyncio.get_running_loop()

    async def _grab(seq: int, start: float, end: float, text: str):
        mid = (start + end) / 2.0
        path = frames_dir / f"seg{seq:03d}.jpg"
        await loop.run_in_executor(
            None,
            lambda: subprocess.run(
                [
                    "ffmpeg", "-y", "-ss", f"{mid:.2f}", "-i", video_path,
                    "-frames:v", "1", "-vf",
                    "scale='min(1024,iw)':-2", "-q:v", "3",
                    str(path),
                ],
                check=True, capture_output=True,
            ),
        )
        return {
            "seq": seq,
            "start_ms": int(start * 1000),
            "end_ms": int(end * 1000),
            "time_ms": int(mid * 1000),
            "path": str(path),
        }

    for i, seg in enumerate(segments):
        try:
            frames.append(await _grab(i, seg["start"], seg["end"], seg.get("text", "")))
        except subprocess.CalledProcessError:
            logger.warning("抽帧失败 seg=%s", i)
    return frames
