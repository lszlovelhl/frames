"""质量门纯函数测试（quality_gate.py，离线）。

覆盖：合格样本通过、关键项一票否决、各阈值触发、权重满分结构、
issues/fixes 对齐。
"""
from __future__ import annotations

import copy

from app.services.three_layer.quality_gate import (
    CRITICAL_KEYS,
    PASS_SCORE,
    WEIGHTS,
    evaluate_quality_gate,
)
from fixtures.sample_data import (
    SAMPLE_CURVE_DEGENERATE,
    SAMPLE_CURVE_OK,
    SAMPLE_LIB_COUNTS_EMPTY,
    good_gate_payload,
)


def test_weights_sum_to_one():
    assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9


def test_good_payload_passes_with_high_score():
    gate = evaluate_quality_gate(good_gate_payload())
    assert gate["passed"] is True
    assert gate["score"] >= PASS_SCORE
    assert not gate["critical_failed"]
    assert all(c["passed"] for c in gate["checks"])
    assert gate["issues"] == []
    assert gate["fixes"] == []


def test_core_idea_missing_is_critical_failure():
    payload = good_gate_payload()
    payload["core_idea"] = ""
    gate = evaluate_quality_gate(payload)
    assert "core_idea" in gate["critical_failed"]
    assert gate["passed"] is False
    # 即使其余项全过、加权分不低，关键项不过仍判 False
    assert any("核心思想" in i for i in gate["issues"])
    assert gate["fixes"]


def test_short_summary_flagged():
    payload = good_gate_payload()
    payload["summary"] = "太短"
    gate = evaluate_quality_gate(payload)
    assert gate["passed"] is False
    assert any("摘要" in i for i in gate["issues"])


def test_low_verbatim_rate_is_critical_failure():
    payload = good_gate_payload()
    payload["quote_verbatim_rate"] = 0.31
    gate = evaluate_quality_gate(payload)
    assert "verbatim_anchor" in gate["critical_failed"]
    assert gate["passed"] is False


def test_empty_lib_counts_fails_reusable_check():
    payload = good_gate_payload()
    payload["lib_counts"] = SAMPLE_LIB_COUNTS_EMPTY
    gate = evaluate_quality_gate(payload)
    assert any(c["key"] == "reusable_elements" and not c["passed"] for c in gate["checks"])
    assert "reusable_elements" not in CRITICAL_KEYS  # 非关键项，不一票否决
    assert any("积木" in i for i in gate["issues"])


def test_degenerate_curve_fails_emotion_check():
    payload = good_gate_payload()
    payload["curve"] = SAMPLE_CURVE_DEGENERATE
    gate = evaluate_quality_gate(payload)
    assert any(c["key"] == "emotion_curve" and not c["passed"] for c in gate["checks"])


def test_missing_curve_reported():
    payload = good_gate_payload()
    payload["curve"] = None
    gate = evaluate_quality_gate(payload)
    assert any("情绪曲线缺失" in i for i in gate["issues"])


def test_rhythm_not_usable_flagged():
    payload = good_gate_payload()
    payload["rhythm"] = {"usable": False, "segment_count": 0, "coverage_ratio": 0.0,
                         "pace_label": "无口播", "note": ""}
    gate = evaluate_quality_gate(payload)
    assert any(c["key"] == "rhythm_curve" and not c["passed"] for c in gate["checks"])


def test_segment_coverage_below_threshold_flagged():
    payload = good_gate_payload()
    payload["segment_coverage"] = 0.6
    gate = evaluate_quality_gate(payload)
    assert "segment_coverage" in gate["critical_failed"]
    assert gate["passed"] is False


def test_issues_and_fixes_count_aligned():
    payload = good_gate_payload()
    payload["core_idea"] = ""
    payload["quote_verbatim_rate"] = 0.2
    payload["lib_counts"] = SAMPLE_LIB_COUNTS_EMPTY
    gate = evaluate_quality_gate(payload)
    assert len(gate["issues"]) == len(gate["fixes"])
    assert gate["score"] < PASS_SCORE


def test_score_between_zero_and_hundred():
    payload = good_gate_payload()
    payload["curve"] = None
    payload["rhythm"] = None
    gate = evaluate_quality_gate(payload)
    assert 0.0 <= gate["score"] <= 100.0


def test_thresholds_snapshot_exposed():
    gate = evaluate_quality_gate(good_gate_payload())
    th = gate["thresholds"]
    assert th["min_hooks"] == 1
    assert th["min_verbatim_rate"] == 0.60
    assert "min_segment_coverage" in th


def test_critical_failure_blocks_pass_even_otherwise_good():
    # 仅缺钩子（关键项），其余全部达标
    payload = good_gate_payload()
    payload["hooks_count"] = 0
    gate = evaluate_quality_gate(payload)
    assert "hook_present" in gate["critical_failed"]
    assert gate["passed"] is False
    assert copy.deepcopy(payload)  # 不修改入参
