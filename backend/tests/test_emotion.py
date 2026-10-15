"""情绪曲线模块（emotion.py）纯逻辑单元测试：采样、插值、峰谷、形状判定。"""
from __future__ import annotations

import pytest

from app.services.three_layer.emotion import (
    analysis,
    build_grid,
    classify_shape,
    direction_change_index,
    index_of_t,
    nearest_t_ms,
    peaks_valleys,
    pick_interval,
    sample_count_for,
    sample_from_spans,
)


def test_pick_interval_default_and_long():
    assert pick_interval(60_000) == 500
    assert pick_interval(200_000) == 1000


def test_build_grid_even_spacing():
    grid = build_grid(60_000, 500)
    assert len(grid) == 121
    assert grid[0] == 0
    assert grid[-1] == 60_000
    assert grid[1] - grid[0] == 500
    assert grid[2] - grid[1] == 500


def test_sample_count_for_matches_grid():
    grid = build_grid(30_000, 500)
    assert sample_count_for(30_000, 500) == len(grid)


def test_sample_from_spans_covers_interval():
    # 覆盖区间为 [s, e) 半开区间：t=0/500/1000 取第一个 span 值，之后取第二个 span 值
    values, interp_idx = sample_from_spans(
        [0, 500, 1000, 1500, 2000],
        [(0, 1000, 2.0), (1000, 2000, 6.0)],
    )
    assert values == [2.0, 2.0, 6.0, 6.0, 6.0]
    assert interp_idx == [4]


def test_sample_from_spans_interp_between_gaps():
    # 两个 span 之间有未覆盖点 → 以 span 中心做线性插值
    values, interp_idx = sample_from_spans(
        [0, 500, 1000, 1500, 2000],
        [(0, 1000, 2.0), (2000, 3000, 6.0)],
    )
    assert values == [2.0, 2.0, 3.0, 4.0, 6.0]
    assert interp_idx == [2, 3]


def test_peaks_valleys_single_peak():
    peaks, valleys = peaks_valleys([0.0, 1.0, 5.0, 1.0, 0.0])
    assert peaks == [2]
    # 平滑后两侧谷被压平（阈值 0.4×(max-min)=2.0 未到），谷列表允许为空


def test_peaks_valleys_flat_no_extremes():
    peaks, valleys = peaks_valleys([5.0, 5.0, 5.0, 5.0])
    assert peaks == []
    assert valleys == []


def test_peaks_valleys_short_series():
    assert peaks_valleys([1.0, 2.0]) == ([], [])


def test_classify_shape_flat():
    assert classify_shape([5.0, 5.0, 5.0], [0, 500, 1000], []) == "平缓"


def test_classify_shape_rising():
    # 单调递增且峰值落点 > 0.85 → 递进上升
    values = [0.0, 1.0, 2.0, 3.0, 4.0]
    grid = [0, 1000, 2000, 3000, 4000]
    peaks, _ = peaks_valleys(values)
    assert classify_shape(values, grid, peaks) == "递进上升"


def test_classify_shape_single_peak_middle():
    values = [0.0, 1.0, 5.0, 1.0, 0.0]
    grid = [0, 1000, 2000, 3000, 4000]
    peaks, _ = peaks_valleys(values)
    assert classify_shape(values, grid, peaks) == "单峰"


def test_analysis_basic_shape():
    values = [0.0, 1.0, 5.0, 1.0, 0.0]
    grid = [0, 1000, 2000, 3000, 4000]
    out = analysis(values, grid)
    assert out["shape"] in {"单峰", "双峰", "波浪", "平缓", "递进上升", "骤升缓降", "前高后低"}
    assert out["series_max"] == 5.0
    assert out["peak_position_ratio"] == pytest.approx(0.5)
    assert out["peak_count"] >= 1


def test_nearest_and_index_of_t():
    grid = [0, 500, 1000]
    assert nearest_t_ms(grid, 480) == 500
    assert index_of_t(grid, 1000) == 2


def test_direction_change_index_flat_is_none():
    assert direction_change_index([5.0, 5.0, 5.0, 5.0]) is None
