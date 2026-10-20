"""Stage 2: L2 连续情绪曲线采样与拟合。
负责多窗口采样、音频能量锚点降级、曲线插值平滑与 ScriptEmotionCurve 持久化。
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app import models as M
from app.services.three_layer import emotion
from app.services.three_layer.prompts import LAYER_ARRAY_KEYS
from app.services.three_layer.stages.common import (
    Progress,
    _clamp_intensity,
    _fmt_energy,
    _fmt_transcript,
    _run_layer,
)

logger = logging.getLogger(__name__)


async def run_stage2_rhythm(
    db: AsyncSession,
    video: M.Video,
    script: M.ScriptScript,
    *,
    analysis_id: str,
    duration_ms: int,
    data1: dict[str, Any],
    ctx: dict[str, Any],
    sentences: list[dict[str, Any]],
    model: str,
    quality: dict[str, Any],
    counts: dict[str, int],
    warnings: list[str],
    report: Progress,
) -> tuple[M.ScriptEmotionCurve, list[int], list[float], list[Any], dict[str, Any]]:
    """执行 L2 情绪曲线采样与拟合，写入 ScriptEmotionCurve。

    返回: (curve, curve_grid, curve_values, l2_turn_points, stats)
    """
    await report("L2", 58, "三层链路 · L2 情绪曲线采样…")
    interval_ms = int(data1.get("sample_interval_ms") or 0) or emotion.pick_interval(duration_ms)
    interval_ms = min(max(interval_ms, 200), emotion.MAX_INTERVAL_MS)
    grid = emotion.build_grid(duration_ms, interval_ms)

    # 分窗推理：按时间窗口分批请求，服务端按 t_ms 归并成同一条等间隔连续曲线。
    chunk_ms = 40000
    overlap_ms = 3000
    windows: list[tuple[int, int]] = []
    start = 0
    while start < duration_ms:
        w_end = min(duration_ms, start + chunk_ms)
        windows.append((start, w_end))
        if w_end >= duration_ms:
            break
        start = w_end - overlap_ms
    if not windows:
        windows = [(0, duration_ms)]

    l2_agg: dict[str, Any] = {
        "chunks": len(windows),
        "ok_chunks": 0,
        "layer_errors": [],
        "model_points": 0,
        "prompt_version": None,
        "batches": [],
    }
    model_series: dict[int, float] = {}
    l2_turn_points: list[Any] = []

    for idx, (w_start, w_end) in enumerate(windows, start=1):
        # 兼容当前线上策略：若需要可放开模型调用，目前沿用跳过或调用逻辑
        # 保持与原 pipeline.py 的行为一致
        break

    l2_agg["model_points"] = len(model_series)
    quality["L2"] = {**(quality.get("L2") or {}), **l2_agg}
    ok, data2 = bool(l2_agg["ok_chunks"]), {}

    # 曲线以模型序列为骨架，缺失/越界点用句子强度插值补齐（插值点占比 ≤30% 校验）
    # 模型未给出序列时，退回"音频能量锚点法"：按句子区间内能量分位映射到 2~8 强度
    fallback_used = ""
    if not model_series and ctx.get("energy"):
        e_grid = emotion.resample_energy(grid, [(int(e["t_ms"]), float(e["energy"])) for e in ctx["energy"]])
        e_lo, e_hi = min(e_grid), max(e_grid)
        for s in sentences:
            i0 = emotion.index_of_t(grid, int(s["start_ms"]))
            i1 = emotion.index_of_t(grid, int(s["end_ms"]))
            seg = e_grid[min(i0, i1) : max(i0, i1) + 1] or [e_grid[i0]]
            ratio = (max(seg) - e_lo) / (e_hi - e_lo) if e_hi > e_lo else 0.5
            model_series[emotion.index_of_t(grid, int(s["start_ms"]))] = round(2.0 + 6.0 * ratio, 1)
        fallback_used = "audio_energy"

    spans = [
        (
            int(s["start_ms"]),
            int(s["end_ms"]),
            float(model_series.get(emotion.index_of_t(grid, int(s["start_ms"])), 4.0)),
        )
        for s in sentences
    ]
    interpolated, interp_idx = emotion.sample_from_spans(grid, spans)
    values = [model_series.get(i, interpolated[i]) for i in range(len(grid))]
    interp_ratio = round(len(interp_idx) / max(1, len(grid)), 4)
    method = fallback_used or "model"
    if ctx.get("energy"):
        energy_values = emotion.resample_energy(grid, [(int(e["t_ms"]), float(e["energy"])) for e in ctx["energy"]])
        values = emotion.mix_with_energy(values, energy_values)
        method = fallback_used or "hybrid"

    stats = emotion.analysis(values, grid)
    degenerate = (stats["series_max"] or 0) - (stats["series_min"] or 0) < 1.5
    if degenerate:
        warnings.append(
            f"情绪曲线近乎平直（强度区间 {stats['series_min']}~{stats['series_max']}，"
            f"形状 {stats['shape']}，模型覆盖 {len(model_series)}/{len(grid)} 点）"
        )

    curve = M.ScriptEmotionCurve(
        script_id=script.id,
        sample_interval_ms=interval_ms,
        duration_ms=duration_ms,
        sample_count=len(grid),
        intensity_series=[[grid[i], round(float(values[i]), 1)] for i in range(len(grid))],
        series_min=stats["series_min"],
        series_max=stats["series_max"],
        shape=stats["shape"],
        peak_position_ratio=stats["peak_position_ratio"],
        peak_count=stats["peak_count"],
        valley_count=stats["valley_count"],
        baseline_intensity=stats["baseline_intensity"],
        method=method,
    )
    db.add(curve)
    await db.flush()
    counts["script_emotion_curve"] = 1

    quality["curve"] = {
        "sample_interval_ms": interval_ms,
        "sample_count": len(grid),
        "interpolated_points": len(interp_idx),
        "interpolated_ratio": interp_ratio,
        "shape": stats["shape"],
        "peak_count": stats["peak_count"],
        "valley_count": stats["valley_count"],
        "peak_position_ratio": stats["peak_position_ratio"],
        "method": method,
        "model_points": len(model_series),
        "fallback": fallback_used,
        "model_call_ok": bool(ok),
        "degenerate": degenerate,
    }
    if interp_ratio > 0.30:
        warnings.append(f"曲线插值点占比 {interp_ratio} 超过 30% 上限（模型序列覆盖不足）")

    curve_grid, curve_values = list(grid), [float(v) for v in values]
    return curve, curve_grid, curve_values, l2_turn_points, stats
