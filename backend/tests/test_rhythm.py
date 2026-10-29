"""节奏推导纯函数测试（rhythm.py，离线）。

口径：节奏 = 段落时长占比 + 句密度 + 停顿转换；与情绪曲线（强度曲线）分离。
"""
from __future__ import annotations

from app.services.three_layer.rhythm import (
    COVERAGE_OK_RATIO,
    PACE_FAST,
    PACE_MID,
    PACE_SLOW,
    PACE_SILENT,
    derive_rhythm,
)
from fixtures.sample_data import DURATION_MS, SAMPLE_SEGMENTS, SAMPLE_SENTENCES


def test_good_sample_covers_all_sentences_and_usable():
    out = derive_rhythm(SAMPLE_SEGMENTS, SAMPLE_SENTENCES, duration_ms=DURATION_MS)
    assert out["coverage_ratio"] == 1.0
    assert out["usable"] is True
    assert out["segment_count"] == 4
    assert out["sentence_count"] == 12
    assert out["pace_label"] in (PACE_SLOW, PACE_MID, PACE_FAST, PACE_SILENT)


def test_segment_share_bounded_and_consolidated():
    out = derive_rhythm(SAMPLE_SEGMENTS, SAMPLE_SENTENCES, duration_ms=DURATION_MS)
    total_share = round(sum(s["share"] for s in out["segments"]), 4)
    # 段落不必逐毫秒铺满（画面空段/停顿不计入），但合计不应超过全片、不应过低
    assert 0.90 <= total_share <= 1.0


def test_long_gap_counted_as_transition():
    sents = [dict(s) for s in SAMPLE_SENTENCES]
    # 在第 5 句后制造 2.2s 长停顿
    sents[4]["end_ms"] = 17200
    sents[5]["start_ms"] = 19400
    out = derive_rhythm(SAMPLE_SEGMENTS, sents, duration_ms=DURATION_MS)
    assert out["transition_count"] >= 1


def test_empty_segments_not_usable():
    out = derive_rhythm([], SAMPLE_SENTENCES, duration_ms=DURATION_MS)
    assert out["usable"] is False
    assert out["coverage_ratio"] == 0.0
    assert out["pace_label"] in (PACE_SLOW, PACE_MID, PACE_FAST, PACE_SILENT)


def test_no_sentences_is_silent():
    out = derive_rhythm(SAMPLE_SEGMENTS, [], duration_ms=DURATION_MS)
    assert out["pace_label"] == PACE_SILENT
    assert out["usable"] is False


def test_low_density_labeled_slow():
    # 两句铺满 120s → 密度极低 → 慢
    sents = [
        {"seq": 1, "start_ms": 0, "end_ms": 5000, "sentence_function": "铺垫"},
        {"seq": 2, "start_ms": 6000, "end_ms": 11000, "sentence_function": "铺垫"},
    ]
    segs = [
        {"seq": 1, "seg_type": "铺垫", "start_ms": 0, "end_ms": 120000,
         "start_sentence_seq": 1, "end_sentence_seq": 2},
    ]
    out = derive_rhythm(segs, sents, duration_ms=120_000)
    assert out["pace_label"] == PACE_SLOW
    assert out["coverage_ratio"] >= COVERAGE_OK_RATIO
