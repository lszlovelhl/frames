"""保真原料层（raw_*）落库：采集产物 → 事实表（《设计方案》第 4 章）。

只写事实，不做任何推断：转写逐句文本与毫秒区间、镜头序号与画面描述、音频物理量、
等间隔音频能量采样。写入前按 ``video_id`` 清理本片旧原料行（幂等重跑），不触碰任何既有旧表。
"""

from __future__ import annotations

import glob
import json
import logging
import os
import subprocess
from typing import Any, Sequence

import numpy as np
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as M
from app.media import ffbin
from app.media.transcribe import parse_timestamped_text
from app.services.zh import has_traditional, to_simplified

logger = logging.getLogger(__name__)

ENERGY_INTERVAL_MS = 500
ENERGY_MAX_SAMPLES = 2400


def _clean_text(text: Any) -> str:
    t = str(text or "").strip()
    if not t:
        return ""
    return to_simplified(t) if has_traditional(t) else t


def _sentences_from_manifest(manifest: dict[str, Any], duration_ms: int) -> list[dict[str, Any]]:
    """优先用 ASR 分段（含起止秒）；退化时按行长度比例铺满片长。"""
    tr = manifest.get("transcript") or {}
    raw_segments: list[dict[str, Any]] = []
    for seg in tr.get("segments") or []:
        text = _clean_text(seg.get("text"))
        if not text:
            continue
        start = int(round(float(seg.get("start") or 0) * 1000))
        end = int(round(float(seg.get("end") or 0) * 1000))
        raw_segments.append({"start_ms": max(0, start), "end_ms": max(start, end), "text": text})

    if not raw_segments:
        for item in parse_timestamped_text(str(tr.get("text") or "")):
            text = _clean_text(item.get("text"))
            if not text:
                continue
            start = int(round(float(item.get("start") or 0) * (1000 if float(item.get("start") or 0) < 1000 else 1)))
            end = int(round(float(item.get("end") or 0) * (1000 if float(item.get("end") or 0) < 1000 else 1)))
            raw_segments.append({"start_ms": max(0, start), "end_ms": max(start, end), "text": text})

    if not raw_segments:
        plain = _clean_text(tr.get("text"))
        lines = [ln.strip() for ln in plain.splitlines() if ln.strip()]
        if lines:
            total_chars = sum(len(ln) for ln in lines) or 1
            cursor = 0
            for ln in lines:
                span = int(duration_ms * len(ln) / total_chars)
                raw_segments.append(
                    {"start_ms": cursor, "end_ms": min(duration_ms, cursor + span), "text": ln}
                )
                cursor += span

    out: list[dict[str, Any]] = []
    for i, seg in enumerate(sorted(raw_segments, key=lambda x: x["start_ms"]), start=1):
        start = max(0, int(seg["start_ms"]))
        end = int(seg["end_ms"])
        if end <= start:
            end = start + 200
        out.append({"seq": i, "start_ms": start, "end_ms": end, "text": seg["text"]})
    return out


def _decode_mono16k(path: str, *, duration_ms: int | None = None) -> np.ndarray | None:
    """用 ffmpeg 直接管道解码为 16k 单声道 float32，避免落任何临时文件。"""
    if not path or not os.path.exists(path):
        return None
    cmd = [
        ffbin.ffmpeg_bin(), "-v", "error", "-i", path,
        "-ac", "1", "-ar", "16000", "-f", "f32le", "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, check=True)
    except Exception:  # noqa: BLE001
        logger.warning("音频解码失败：%s", path, exc_info=True)
        return None
    audio = np.frombuffer(proc.stdout, dtype=np.float32)
    if audio.size == 0:
        return None
    max_len = int(16000 * (ENERGY_MAX_SAMPLES * ENERGY_INTERVAL_MS / 1000))
    if audio.size > max_len:
        audio = audio[:max_len]
    return audio


def energy_samples(audio: np.ndarray, *, interval_ms: int = ENERGY_INTERVAL_MS) -> list[dict[str, Any]]:
    """等间隔 RMS 能量（归一化 0~1），能量低的静音段也保留原始形态。"""
    sr = 16000
    win = int(sr * interval_ms / 1000)
    if win <= 0:
        return []
    n = audio.size // win
    if n <= 0:
        return []
    frames = audio[: n * win].reshape(n, win)
    rms = np.sqrt(np.mean(np.square(frames.astype(np.float64)), axis=1))
    peak = float(np.percentile(rms, 95)) or float(rms.max()) or 1.0
    norm = np.clip(rms / peak, 0.0, 1.0)
    return [
        {"seq": i + 1, "t_ms": i * interval_ms, "energy": round(float(v), 4)}
        for i, v in enumerate(norm)
    ]


def find_audio_file(manifest: dict[str, Any]) -> str | None:
    work_dir = str(manifest.get("work_dir") or "")
    for pattern in ("*.wav", "audio_16k.wav", "audio_track.m4a", "audio_track.*"):
        hits = sorted(glob.glob(os.path.join(work_dir, pattern)))
        for hit in hits:
            if hit.lower().endswith((".wav", ".m4a", ".mp3", ".aac")):
                return hit
    video_path = str(manifest.get("video_path") or "")
    return video_path if video_path and os.path.exists(video_path) else None


async def persist_raw_layer(
    db: AsyncSession, video: M.Video, manifest: dict[str, Any]
) -> dict[str, Any]:
    """把采集清单写入 raw_* 五表，返回后续层需要的原料上下文。"""
    duration_ms = int(manifest.get("duration_ms") or manifest.get("effective_duration_ms") or video.duration_ms or 0)
    platform = str(manifest.get("platform") or video.platform or "unknown")
    meta = manifest.get("meta") or {}
    work_dir = str(manifest.get("work_dir") or "")
    video_path = str(manifest.get("video_path") or "")
    tr = manifest.get("transcript") or {}
    audio = manifest.get("audio") or {}
    bgm = manifest.get("bgm") or {}

    src_mode = str(meta.get("subtitle_mode") or meta.get("transcript_source") or "")
    subtitle_source = "ocr" if src_mode == "ocr_only" else "asr"

    # 幂等：先清本片原料行
    for model in (M.RawTranscriptSentence, M.RawShot, M.RawAudioEnergySample):
        await db.execute(delete(model).where(model.video_id == video.id))

    row_manifest = (
        await db.execute(select(M.RawMediaManifest).where(M.RawMediaManifest.video_id == video.id))
    ).scalar_one_or_none()
    if row_manifest is None:
        row_manifest = M.RawMediaManifest(video_id=video.id, platform=platform, work_dir=work_dir, duration_ms=duration_ms, subtitle_source=subtitle_source)
        db.add(row_manifest)
    row_manifest.platform = platform
    row_manifest.work_dir = work_dir
    row_manifest.video_path = video_path or None
    row_manifest.duration_ms = duration_ms
    row_manifest.subtitle_source = subtitle_source
    row_manifest.asr_model = (str(tr.get("model")) or None) if tr.get("model") else None
    row_manifest.asr_pipeline_version = str(meta.get("pipeline_version") or "")[:16] or None
    row_manifest.warnings = list(manifest.get("warnings") or [])

    sentences = _sentences_from_manifest(manifest, duration_ms)
    raw_sentences: list[M.RawTranscriptSentence] = []
    for item in sentences:
        row = M.RawTranscriptSentence(
            video_id=video.id,
            seq=int(item["seq"]),
            start_ms=int(item["start_ms"]),
            end_ms=int(item["end_ms"]),
            text=str(item["text"]),
            source=subtitle_source,
            proofread=1 if tr.get("proofread") else 0,
            confidence=None,
        )
        db.add(row)
        raw_sentences.append(row)

    raw_shots: list[M.RawShot] = []
    frames = sorted(
        (f for f in (manifest.get("frames") or []) if _clean_text(f.get("desc"))),
        key=lambda f: int(f.get("start_ms") or f.get("time_ms") or 0),
    )
    for idx, frame in enumerate(frames, start=1):
        desc = _clean_text(frame.get("desc"))
        kind = str(frame.get("kind") or "frame")
        if kind not in ("anchor", "key", "frame"):
            kind = "frame"
        row = M.RawShot(
            video_id=video.id,
            # 采集侧 seq 存在 0 基且可能重复，落库时按开始时间重排为连续序号
            seq=idx,
            kind=kind,
            start_ms=int(frame.get("start_ms") or frame.get("time_ms") or 0),
            end_ms=int(frame.get("end_ms") or frame.get("time_ms") or 0),
            desc=desc,
            path=str(frame.get("path") or "") or None,
            text_overlay=_clean_text(frame.get("text_overlay")) or None,
            style=str(frame.get("style") or "")[:32] or None,
        )
        db.add(row)
        raw_shots.append(row)

    profile = (
        await db.execute(select(M.RawAudioProfile).where(M.RawAudioProfile.video_id == video.id))
    ).scalar_one_or_none()
    if profile is None:
        profile = M.RawAudioProfile(video_id=video.id)
        db.add(profile)
    profile.bpm = int(audio.get("bpm")) if audio.get("bpm") else None
    profile.mean_volume_db = float(audio["mean_volume_db"]) if audio.get("mean_volume_db") is not None else None
    profile.has_bgm = 1 if audio.get("has_bgm") or bgm.get("ok") else 0
    profile.bgm_note = (str(bgm.get("method") or bgm.get("note") or "") or None) if (bgm or audio) else None

    energy_rows: list[dict[str, Any]] = []
    audio_file = find_audio_file(manifest)
    audio_arr = _decode_mono16k(audio_file, duration_ms=duration_ms) if audio_file else None
    if audio_arr is not None:
        energy_rows = energy_samples(audio_arr)
        for item in energy_rows:
            db.add(
                M.RawAudioEnergySample(
                    video_id=video.id,
                    seq=int(item["seq"]),
                    t_ms=int(item["t_ms"]),
                    energy=float(item["energy"]),
                )
            )

    await db.flush()
    return {
        "duration_ms": duration_ms,
        "platform": platform,
        "sentences": [
            {"id": r.id, "seq": r.seq, "start_ms": r.start_ms, "end_ms": r.end_ms, "text": r.text}
            for r in raw_sentences
        ],
        "shots": [
            {"id": r.id, "seq": r.seq, "start_ms": r.start_ms, "end_ms": r.end_ms, "desc": r.desc}
            for r in raw_shots
        ],
        "energy": energy_rows,
        "has_bgm": bool(profile.has_bgm),
        "bpm": profile.bpm,
    }


async def load_raw_context(db: AsyncSession, video_id) -> dict[str, Any]:
    """从 raw_* 读回原料上下文（重跑/续跑时不必重新采集）。"""
    sentences = (
        await db.execute(
            select(M.RawTranscriptSentence)
            .where(M.RawTranscriptSentence.video_id == video_id)
            .order_by(M.RawTranscriptSentence.seq)
        )
    ).scalars().all()
    shots = (
        await db.execute(
            select(M.RawShot).where(M.RawShot.video_id == video_id).order_by(M.RawShot.seq)
        )
    ).scalars().all()
    energy = (
        await db.execute(
            select(M.RawAudioEnergySample)
            .where(M.RawAudioEnergySample.video_id == video_id)
            .order_by(M.RawAudioEnergySample.seq)
        )
    ).scalars().all()
    manifest = (
        await db.execute(select(M.RawMediaManifest).where(M.RawMediaManifest.video_id == video_id))
    ).scalar_one_or_none()
    profile = (
        await db.execute(select(M.RawAudioProfile).where(M.RawAudioProfile.video_id == video_id))
    ).scalar_one_or_none()
    duration_ms = int(
        (manifest.duration_ms if manifest else 0)
        or (sentences[-1].end_ms if sentences else 0)
        or 0
    )
    return {
        "duration_ms": duration_ms,
        "platform": (manifest.platform if manifest else None) or "unknown",
        "sentences": [
            {"id": r.id, "seq": r.seq, "start_ms": r.start_ms, "end_ms": r.end_ms, "text": r.text}
            for r in sentences
        ],
        "shots": [
            {"id": r.id, "seq": r.seq, "start_ms": r.start_ms, "end_ms": r.end_ms, "desc": r.desc}
            for r in shots
        ],
        "energy": [{"seq": r.seq, "t_ms": r.t_ms, "energy": r.energy} for r in energy],
        "has_bgm": bool(profile.has_bgm) if profile else False,
        "bpm": profile.bpm if profile else None,
    }
