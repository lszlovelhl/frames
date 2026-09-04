"""faster-whisper 全量语音转写（ASR），输出带时间戳的分句文本。

统一用 ASR 拿全文案（不再依赖平台字幕），保证多平台口径一致。
模型加载优先级：
1. 环境变量 WHISPER_MODEL_PATH：本地 ctranslate2 模型目录（离线/镜像下载路径）；
2. 环境变量 WHISPER_MODEL（默认 medium）：HF 模型名，首次自动下载。
中文推荐 small / medium / large-v3（small 已可满足拆解文案用途且 CPU 快）。
"""
import logging
import os
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)

MODEL_SPEC = os.getenv("WHISPER_MODEL_PATH") or os.getenv("WHISPER_MODEL", "medium")

_model_cache: dict = {}


def _ensure_wav16k(video_path: str, work_dir: Path) -> Path:
    """转 16k 单声道 wav，faster-whisper 稳定读取且省解码。"""
    wav = work_dir / "audio_16k.wav"
    if wav.exists():
        return wav
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", video_path, "-vn",
            "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav),
        ],
        check=True,
        capture_output=True,
    )
    return wav


def _get_model(spec: str):
    from faster_whisper import WhisperModel

    if spec not in _model_cache:
        logger.info("加载 faster-whisper 模型 %s ...", spec)
        t0 = time.time()
        _model_cache[spec] = WhisperModel(spec, device="cpu", compute_type="int8")
        logger.info("whisper %s 就绪，耗时 %.1fs", spec, time.time() - t0)
    return _model_cache[spec]


async def transcribe(video_path: str, work_dir: Path) -> dict:
    """返回 {language, text, segments:[{start,end,text}], duration}"""
    t0 = time.time()
    wav = _ensure_wav16k(video_path, work_dir)
    model = _get_model(MODEL_SPEC)

    import asyncio

    loop = asyncio.get_running_loop()

    def _run():
        segs, info = model.transcribe(
            str(wav),
            # 默认关闭 VAD：MV/BGM 混音下人声会被误滤导致文案缺失，
            # 全量转写保证拿全所有语音；纯口播视频也多花不了多少时间
            vad_filter=False,
            beam_size=5,
            word_timestamps=False,
        )
        language = info.language
        duration = info.duration
        items = []
        for s in segs:
            items.append(
                {
                    "start": round(float(s.start), 3),
                    "end": round(float(s.end), 3),
                    "text": s.text.strip(),
                }
            )
        return language, duration, items

    try:
        language, duration, items = await loop.run_in_executor(None, _run)
    except Exception:  # 模型加载/解码失败兜底重试一次
        language, duration, items = await loop.run_in_executor(None, _run)

    items = [s for s in items if s["text"]]
    text = "\n".join(s["text"] for s in items)
    logger.info(
        "ASR 完成: lang=%s dur=%.0fs segs=%d chars=%d 耗时 %.0fs",
        language, duration, len(items), len(text), time.time() - t0,
    )
    return {
        "language": language,
        "text": text,
        "segments": items,
        "duration": duration,
        "model": MODEL_SPEC,
    }
