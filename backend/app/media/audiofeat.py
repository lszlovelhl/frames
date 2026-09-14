"""声学特征 + BGM 分离。

- estimate_bpm: numpy 能量包络自相关（纯 CPU，无重依赖）
- mean_volume_db: ffmpeg volumedetect
- split_bgm: demucs 分离 vocal / bgm；未装 torch/demucs 时降级为空
"""
import logging
import subprocess
import wave
from pathlib import Path
from typing import Any

from app.media import ffbin

logger = logging.getLogger(__name__)


def mean_volume_db(video_path: str) -> float | None:
    try:
        out = subprocess.run(
            [ffbin.ffmpeg_bin(), "-i", video_path, "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True,
        )
        for line in out.stderr.splitlines():
            if "mean_volume" in line:
                return float(line.split(":")[-1].strip().replace(" dB", ""))
    except Exception:  # noqa: BLE001
        pass
    return None


def estimate_bpm(wav_path: Path, sr: int = 16000) -> int | None:
    """基于 16k 单声道 wav 能量包络的自相关 BPM 估算（60-180bpm）。"""
    try:
        import numpy as np
    except ImportError:
        return None
    try:
        with wave.open(str(wav_path), "rb") as wf:
            if wf.getframerate() != sr or wf.getnchannels() != 1:
                return None
            n = wf.getnframes()
            raw = wf.readframes(min(n, sr * 180))
        data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        frame_len, hop = 1024, 512
        # 只保留完整帧，避免末尾不足一帧导致 reshape 失败
        n_frames = len(data) // frame_len
        if n_frames < 200:
            return None
        frames = data[: n_frames * frame_len].reshape(-1, frame_len)  # 简化分帧
        env = np.sqrt((frames**2).mean(axis=1))
        env = env - env.mean()
        # 非重叠分帧 → 包络采样率 = sr / frame_len
        fs_env = sr / frame_len  # 15.625 Hz
        lo = max(3, int(fs_env * 60 / 180))  # 180 bpm
        hi = int(fs_env * 60 / 60) + 1  # 60 bpm
        corr = np.correlate(env, env, "full")[len(env) - 1:]
        corr /= corr[0] + 1e-9
        seg = corr[lo:hi]
        if len(seg) < 3 or float(seg.max()) < 0.08:
            return None
        peak = int(np.argmax(seg)) + lo
        bpm = fs_env * 60.0 / peak
        # 还原常见倍速误差到 60-180 区间
        while bpm < 60:
            bpm *= 2
        while bpm > 180:
            bpm /= 2
        return int(round(bpm))
    except Exception:  # noqa: BLE001
        logger.warning("BPM 估算失败", exc_info=True)
        return None


def split_bgm(audio_path: str, out_dir: Path, model: str = "htdemucs") -> dict[str, Any]:
    """demucs 分离 vocals / no_vocal(bgm)。返回文件路径或失败原因。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        from demucs.pretrained import get_model
        from demucs.apply import apply_model
        import torch
        import torchaudio
    except ImportError:
        return {"ok": False, "reason": "demucs/torch 未安装（BGM 分离暂不可用）"}

    try:
        import numpy as np

        audio, sr = torchaudio.load(audio_path)
        if audio.shape[0] > 1:
            audio = audio.mean(dim=0, keepdim=True)
        if sr != 44100:
            audio = torchaudio.functional.resample(audio, sr, 44100)
            sr = 44100
        # 长音频截断到 10 分钟，控制显存/时间
        max_len = sr * 600
        if audio.shape[1] > max_len:
            audio = audio[:, :max_len]

        model_obj = get_model(model)
        ref = model_obj.samplerate
        if ref != sr:
            audio = torchaudio.functional.resample(audio, sr, ref)
        if audio.shape[0] == 1:
            audio = audio.repeat(2, 1)
        with torch.no_grad():
            sources = apply_model(model_obj, audio[None], device="cpu", progress=False)[0]
        names = model_obj.sources  # ['drums','bass','other','vocals']
        stems: dict[str, Path] = {}
        vocals = None
        for i, name in enumerate(names):
            stem = sources[i]
            if ref != 44100:
                stem = torchaudio.functional.resample(stem, ref, 44100)
            p = out_dir / f"{name}.wav"
            torchaudio.save(str(p), stem[None], 44100)
            stems[name] = p
            if name == "vocals":
                vocals = stem
        # bgm = no_vocal 合轨
        bgm_path = out_dir / "bgm.wav"
        if vocals is not None:
            non_vocal = torch.zeros_like(vocals)
            for name, src in zip(names, sources):
                if name != "vocals":
                    non_vocal = non_vocal + src
            if ref != 44100:
                non_vocal = torchaudio.functional.resample(non_vocal, ref, 44100)
            torchaudio.save(str(bgm_path), non_vocal[None], 44100)
            vocal_energy = float((vocals**2).mean().sqrt())
            bgm_energy = float((non_vocal**2).mean().sqrt())
            return {
                "ok": True,
                "vocals_path": str(stems.get("vocals", "")),
                "bgm_path": str(bgm_path),
                "vocal_ratio": round(vocal_energy / (bgm_energy + 1e-9), 3),
            }
        return {"ok": True, "bgm_path": str(bgm_path)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("BGM 分离失败", exc_info=True)
        return {"ok": False, "reason": str(exc)[:200]}
