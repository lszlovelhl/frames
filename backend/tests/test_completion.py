"""完播驱动要素推导纯函数测试（completion.py，离线）。"""
from __future__ import annotations

from app.services.three_layer.completion import (
    DRIVER_CATALOG,
    DRIVER_CTA_CLOSE,
    DRIVER_CURIOUSITY_GAP,
    DRIVER_HOOK_OPEN,
    DRIVER_CLIMAX_PAYOFF,
    DRIVER_VALUE_DENSITY,
    MAX_DRIVERS,
    derive_completion_drivers,
    summarize_completion_drivers,
)
from fixtures.sample_data import DURATION_MS, SAMPLE_SEGMENTS, SAMPLE_SENTENCES


def _types(drivers):
    return {d["driver"] for d in drivers}


def test_all_five_driver_types_detected_from_sample():
    drivers = derive_completion_drivers(
        SAMPLE_SEGMENTS, SAMPLE_SENTENCES, duration_ms=DURATION_MS
    )
    types = _types(drivers)
    for t in (
        DRIVER_HOOK_OPEN,
        DRIVER_CURIOUSITY_GAP,
        DRIVER_CLIMAX_PAYOFF,
        DRIVER_VALUE_DENSITY,
        DRIVER_CTA_CLOSE,
    ):
        assert t in types


def test_entries_carry_quote_time_and_strength():
    drivers = derive_completion_drivers(
        SAMPLE_SEGMENTS, SAMPLE_SENTENCES, duration_ms=DURATION_MS
    )
    for d in drivers:
        assert d["label"] == DRIVER_CATALOG[d["driver"]]["label"]
        assert isinstance(d["t_ms"], int)
        assert 0.0 <= d["strength"] <= 1.0
        assert d["source"] in ("sentence", "segment", "lib_hook")


def test_drivers_sorted_by_time_and_capped():
    drivers = derive_completion_drivers(
        SAMPLE_SEGMENTS, SAMPLE_SENTENCES, duration_ms=DURATION_MS
    )
    ts = [d["t_ms"] for d in drivers]
    assert ts == sorted(ts)
    assert len(drivers) <= MAX_DRIVERS


def test_hook_open_strength_higher_in_first_fifth():
    drivers = derive_completion_drivers(
        SAMPLE_SEGMENTS, SAMPLE_SENTENCES, duration_ms=DURATION_MS
    )
    hooks = [d for d in drivers if d["driver"] == DRIVER_HOOK_OPEN]
    assert hooks
    assert max(h["strength"] for h in hooks) >= 0.7


def test_summarize_counts_and_strongest():
    drivers = derive_completion_drivers(
        SAMPLE_SEGMENTS, SAMPLE_SENTENCES, duration_ms=DURATION_MS
    )
    summary = summarize_completion_drivers(drivers)
    assert summary["count"] == len(drivers)
    assert summary["has_hook_open"] is True
    assert summary["has_cta_close"] is True
    assert summary["strongest"]["strength"] == max(d["strength"] for d in drivers)


def test_empty_inputs_return_empty_list():
    assert derive_completion_drivers([], [], duration_ms=10_000) == []
    assert summarize_completion_drivers([])["count"] == 0
