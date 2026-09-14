"""连续情绪曲线的采样、插值、峰谷与形状判定（《设计方案》第 6.2~6.5 节）。

设计要点：
- 采样网格 ``t_k = k × interval``，末点对齐片尾；
- 强度值 0~10 一位小数；序列点占比中"线性插值点"不得超过 30%；
- ``hybrid`` 混合模式下音频能量局部突增处强度不得低于相邻采样点均值；
- 峰/谷判定采用"局部极值 + 幅度阈值（0.4 × (max − min)）"；
- 形状由 ``peak_count`` / 峰值落点 ``r`` / 上升下降段占比 联合判定，见 6.3 节判定表。
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Sequence

SHAPES = ("单峰", "双峰", "递进上升", "波浪", "骤升缓降", "前高后低", "平缓")

DEFAULT_INTERVAL_MS = 500
MAX_INTERVAL_MS = 2000


def pick_interval(duration_ms: int) -> int:
    """采样间隔：默认 500ms，> 3 分钟用 1000ms，上限 2000ms（6.2 节）。"""
    if duration_ms > 180_000:
        return 1000
    return DEFAULT_INTERVAL_MS


def build_grid(duration_ms: int, interval_ms: int) -> list[int]:
    """等间隔采样网格，末点对齐片尾。"""
    if duration_ms <= 0:
        return [0]
    interval_ms = max(1, min(int(interval_ms or DEFAULT_INTERVAL_MS), MAX_INTERVAL_MS))
    n = int(duration_ms // interval_ms)
    grid = [k * interval_ms for k in range(n + 1)]
    if grid[-1] != duration_ms:
        grid[-1] = duration_ms
    return grid


def sample_count_for(duration_ms: int, interval_ms: int) -> int:
    return len(build_grid(duration_ms, interval_ms))


def _spans(spans: Sequence[tuple[int, int, float]]) -> list[tuple[int, int, float]]:
    """(start_ms, end_ms, intensity) 归一化为有序、非退化区间。"""
    out: list[tuple[int, int, float]] = []
    for start, end, val in spans:
        s = max(0, int(start or 0))
        e = max(s, int(end or 0))
        out.append((s, e, float(max(0.0, min(10.0, val or 0.0)))))
    out.sort(key=lambda x: x[0])
    return out


def _interp(t: int, lo: tuple[int, float], hi: tuple[int, float]) -> float:
    t0, v0 = lo
    t1, v1 = hi
    if t1 <= t0:
        return v1
    k = (t - t0) / (t1 - t0)
    return v0 + (v1 - v0) * k


def sample_from_spans(
    grid: Sequence[int], spans: Sequence[tuple[int, int, float]]
) -> tuple[list[float], list[int]]:
    """按句子区间采样：覆盖点取区间值，未覆盖点线性插值（或缺省端点值）。

    返回 (强度序列, 插值点下标列表)。
    """
    sp = _spans(spans)
    values: list[float] = []
    interp_idx: list[int] = []
    if not sp:
        return [0.0 for _ in grid], list(range(len(grid)))

    centers = [((s + e) / 2.0, v) for s, e, v in sp]
    for i, t in enumerate(grid):
        hit = None
        for s, e, v in sp:
            if s <= t < e or (t == e and s == e):
                hit = v
                break
        if hit is not None:
            values.append(hit)
            continue
        interp_idx.append(i)
        if t < sp[0][0]:
            values.append(sp[0][2])
        elif t >= sp[-1][1]:
            values.append(sp[-1][2])
        else:
            lo = max((c for c in centers if c[0] <= t), key=lambda c: c[0], default=None)
            hi = min((c for c in centers if c[0] > t), key=lambda c: c[0], default=None)
            if lo is None:
                values.append(sp[0][2])
            elif hi is None:
                values.append(sp[-1][2])
            else:
                values.append(_interp(t, lo, hi))
    return values, interp_idx


def resample_energy(grid: Sequence[int], samples: Sequence[tuple[int, float]]) -> list[float]:
    """把音频能量采样（t_ms, 0~1）重采样到网格（线性）。"""
    pts = sorted((int(t or 0), float(v or 0.0)) for t, v in samples)
    if not pts:
        return [0.0 for _ in grid]
    out: list[float] = []
    for t in grid:
        if t <= pts[0][0]:
            out.append(pts[0][1])
            continue
        if t >= pts[-1][0]:
            out.append(pts[-1][1])
            continue
        for i in range(1, len(pts)):
            if pts[i][0] >= t:
                out.append(_interp(t, pts[i - 1], pts[i]))
                break
    return out


def mix_with_energy(
    model_values: Sequence[float], energy_values: Sequence[float], *, weight: float = 0.25
) -> list[float]:
    """``hybrid`` 混合校准：模型强度为主，音频能量做局部校准。

    - 全局：``v = (1-w) * model + w * energy*10``；
    - 局部：能量突增处（> 相邻均值 1.5 倍）的强度取 ``max(v, 相邻均值)``。
    """
    n = min(len(model_values), len(energy_values))
    out = [
        (1 - weight) * float(model_values[i]) + weight * float(energy_values[i]) * 10.0
        for i in range(n)
    ]
    for i in range(n):
        lo = out[i - 1] if i > 0 else out[i]
        hi = out[i + 1] if i + 1 < n else out[i]
        neighbor_mean = (lo + hi) / 2.0
        e_lo = energy_values[i - 1] if i > 0 else energy_values[i]
        e_hi = energy_values[i + 1] if i + 1 < n else energy_values[i]
        e_mean = max(1e-6, (float(e_lo) + float(e_hi)) / 2.0)
        if float(energy_values[i]) > 1.5 * e_mean:
            out[i] = max(out[i], neighbor_mean)
    return [round(max(0.0, min(10.0, v)), 1) for v in out]


def peaks_valleys(values: Sequence[float]) -> tuple[list[int], list[int]]:
    """峰/谷下标：局部极值 + 幅度阈值 0.4 × (max − min)（6.3 节）。"""
    n = len(values)
    if n < 3:
        return [], []
    series_max, series_min = max(values), min(values)
    thresh = 0.4 * (series_max - series_min)
    peaks: list[int] = []
    valleys: list[int] = []
    for i in range(1, n - 1):
        v, prev, nxt = values[i], values[i - 1], values[i + 1]
        if v >= prev and v >= nxt and (v > prev or v > nxt):
            if v - series_min >= thresh:
                peaks.append(i)
        if v <= prev and v <= nxt and (v < prev or v < nxt):
            if series_max - v >= thresh:
                valleys.append(i)
    return peaks, valleys


def classify_shape(values: Sequence[float], grid: Sequence[int], peaks: Sequence[int]) -> str:
    """形状判定（6.3 节判定表，按顺序命中即返回）。"""
    if not values:
        return "平缓"
    series_max, series_min = max(values), min(values)
    if series_max - series_min < 2:
        return "平缓"

    duration = max(1, int(grid[-1]))
    argmax = max(range(len(values)), key=lambda i: values[i])
    r = grid[argmax] / duration
    rise_end = grid[argmax] if argmax > 0 else grid[0]
    fall_start = grid[argmax]
    rise = rise_end / duration
    fall = (duration - fall_start) / duration

    monotonic = all(
        values[i + 1] - values[i] >= -0.5 for i in range(len(values) - 1)
    )
    if monotonic and r > 0.85:
        return "递进上升"
    if len(peaks) == 1 and 0.2 <= r <= 0.8:
        return "单峰"
    if len(peaks) == 1 and rise < 0.25 and fall > 0.5:
        return "骤升缓降"
    if len(peaks) == 1 and r < 0.25:
        return "前高后低"
    if len(peaks) == 2:
        return "双峰"
    if len(peaks) >= 3:
        return "波浪"
    if len(peaks) == 1:
        return "单峰"
    return "波浪"


def peak_position_ratio(values: Sequence[float], grid: Sequence[int]) -> float:
    """峰值落点比率：最高峰时刻 / 总时长（多峰等高取最早）。"""
    if not values:
        return 0.0
    duration = max(1, int(grid[-1]))
    best = max(values)
    idx = next(i for i, v in enumerate(values) if v == best)
    return round(grid[idx] / duration, 4)


def analysis(
    values: Sequence[float], grid: Sequence[int]
) -> dict[str, Any]:
    """一次性产出曲线统计量：峰谷数与形状、峰值落点、极值、基线、上升下降占比。"""
    peaks, valleys = peaks_valleys(values)
    shape = classify_shape(values, grid, peaks)
    duration = max(1, int(grid[-1]))
    argmax = max(range(len(values)), key=lambda i: values[i]) if values else 0
    return {
        "series_min": round(min(values), 1) if values else None,
        "series_max": round(max(values), 1) if values else None,
        "shape": shape,
        "peak_position_ratio": peak_position_ratio(values, grid),
        "peak_count": len(peaks),
        "valley_count": len(valleys),
        "baseline_intensity": round(sum(values) / len(values), 1) if values else None,
        "rise_ratio": round(grid[argmax] / duration, 4),
        "fall_ratio": round((duration - grid[argmax]) / duration, 4),
        "peaks": peaks,
        "valleys": valleys,
    }


def nearest_t_ms(grid: Sequence[int], t_ms: int) -> int:
    if not grid:
        return int(t_ms or 0)
    return min(grid, key=lambda g: abs(g - int(t_ms or 0)))


def index_of_t(grid: Sequence[int], t_ms: int) -> int:
    if not grid:
        return 0
    return min(range(len(grid)), key=lambda i: abs(grid[i] - int(t_ms or 0)))


def direction_change_index(values: Sequence[float], *, window_ratio: float = 0.15) -> int | None:
    """由升转降最剧烈的点（用于"反转"转折点）。"""
    n = len(values)
    if n < 4:
        return None
    w = max(2, int(n * window_ratio))
    best_idx, best_drop = None, 0.0
    for i in range(1, n - 1):
        if values[i] < values[i - 1]:
            continue
        tail = values[i + 1 : i + 1 + w]
        if not tail:
            continue
        drop = values[i] - min(tail)
        if drop > best_drop:
            best_idx, best_drop = i, drop
    return best_idx


def suspension_index(values: Sequence[float], *, window_ratio: float = 0.1) -> int | None:
    """悬念点：下降前出现的短暂平台（局部极小变化后的抬升前）——取最长的低波动段中点。"""
    n = len(values)
    if n < 6:
        return None
    w = max(2, int(n * window_ratio))
    best_idx, best_score = None, None
    for i in range(n - w):
        seg = values[i : i + w]
        fluctuation = max(seg) - min(seg)
        score = fluctuation
        if best_score is None or score < best_score:
            best_score, best_idx = score, i + w // 2
    return best_idx
