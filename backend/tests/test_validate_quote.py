"""R8 原话一致度（逐字链定位 + 跨句拼接 + 回指 + 相似度兜底）单元测试。"""
from __future__ import annotations

from app.services.three_layer.validate import normalize_quote

CHAIN = [
    {"id": "r1", "seq": 1, "text": "今天天气很好"},
    {"id": "r2", "seq": 2, "text": "我们出去玩"},
]


def _validator():
    from app.services.three_layer.validate import Validator

    return Validator(duration_ms=60_000, raw_chain=CHAIN)


def test_normalize_quote_keeps_real_chars():
    assert normalize_quote("剧情、多人、士兵模式") == "剧情多人士兵模式"


def test_quote_span_single_sentence():
    v = _validator()
    span = v.quote_span("天气很好")
    assert span is not None
    assert span["span"] == 1
    assert span["raw_ids"] == ["r1"]


def test_quote_span_cross_sentence():
    v = _validator()
    span = v.quote_span("很好我们")
    assert span is not None
    assert span["span"] == 2
    assert span["raw_ids"] == ["r1", "r2"]


def test_quote_span_not_contiguous():
    v = _validator()
    assert v.quote_span("完全不存在的话") is None


def test_r8_exact_substring_pass(make_validator, make_verdict):
    v = _validator()
    verdict = make_verdict(row_type="lib_quote")
    v.r8_quote({"quote": "今天天气很好", "evidence_type": "transcript"}, verdict)
    assert not verdict.hard
    assert not verdict.soft


def test_r8_cross_sentence_records_corrected(make_validator, make_verdict):
    v = _validator()
    verdict = make_verdict(row_type="lib_quote")
    v.r8_quote({"quote": "天气很好我们出去", "evidence_type": "transcript", "raw_sentence_id": "r1"}, verdict)
    assert not verdict.hard
    assert any("跨句拼接" in m for m in verdict.corrected)


def test_r8_ellipsis_is_hard(make_validator, make_verdict):
    v = _validator()
    verdict = make_verdict(row_type="lib_quote")
    v.r8_quote({"quote": "今天天气很好…", "evidence_type": "transcript"}, verdict)
    assert any(m.startswith("R8") and "省略" in m for m in verdict.hard)


def test_r8_anchor_mismatch_is_hard(make_validator, make_verdict):
    v = _validator()
    verdict = make_verdict(row_type="lib_quote")
    v.r8_quote({"quote": "天气很好", "evidence_type": "transcript", "raw_sentence_id": "r2"}, verdict)
    assert any(m.startswith("R8") and "回指不实" in m for m in verdict.hard)


def test_r8_non_transcript_skipped(make_validator, make_verdict):
    v = _validator()
    verdict = make_verdict(row_type="lib_quote")
    v.r8_quote({"quote": "随便写的一句话", "evidence_type": "ocr"}, verdict)
    assert not verdict.hard
    assert not verdict.soft


def test_r8_unmatched_quote_hard(make_validator, make_verdict):
    v = _validator()
    verdict = make_verdict(row_type="lib_quote")
    v.r8_quote({"quote": "这句话完全不在原料里出现", "evidence_type": "transcript"}, verdict)
    assert any(m.startswith("R8") and "一致度不足" in m for m in verdict.hard)


def test_r8_empty_quote_skipped(make_validator, make_verdict):
    v = _validator()
    verdict = make_verdict(row_type="lib_quote")
    v.r8_quote({"quote": "", "evidence_type": "transcript"}, verdict)
    assert not verdict.hard
    assert not verdict.soft
