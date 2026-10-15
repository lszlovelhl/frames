"""修复4 W3：R8 按来源分流（quote_source 非 transcript → 软校验）单元测试。

验证：
- transcript 严格判定链不变（未命中仍硬拦截）；
- OCR 分流：省略标记/未命中/回指不覆盖一律记 soft，不硬拦截；逐字命中照常通过；
- _raw_source_of 来源判定优先级。
"""
from __future__ import annotations

CHAIN = [
    {"id": "r1", "seq": 1, "text": "今天天气很好"},
    {"id": "r2", "seq": 2, "text": "我们出去玩"},
]


def _validator():
    from app.services.three_layer.validate import Validator

    return Validator(duration_ms=60_000, raw_chain=CHAIN)


def test_r8_transcript_unmatched_still_hard(make_validator, make_verdict):
    v = _validator()
    verdict = make_verdict(row_type="lib_quote")
    v.r8_quote({"quote": "这句话完全不在原料里出现", "evidence_type": "transcript"}, verdict)
    assert any(m.startswith("R8") and "一致度不足" in m for m in verdict.hard)


def test_r8_ocr_ellipsis_is_soft_not_hard(make_validator, make_verdict):
    v = _validator()
    verdict = make_verdict(row_type="lib_quote")
    v.r8_quote({"quote": "今天天气很好…", "evidence_type": "transcript", "subtitle_source": "ocr"}, verdict,
               quote_source="ocr")
    assert not verdict.hard
    assert any("省略标记" in m for m in verdict.soft)


def test_r8_ocr_exact_substring_passes(make_validator, make_verdict):
    v = _validator()
    verdict = make_verdict(row_type="lib_quote")
    v.r8_quote({"quote": "今天天气很好", "evidence_type": "transcript", "subtitle_source": "ocr"}, verdict,
               quote_source="ocr")
    assert not verdict.hard
    assert not verdict.soft


def test_r8_ocr_cross_sentence_records_corrected(make_validator, make_verdict):
    v = _validator()
    verdict = make_verdict(row_type="lib_quote")
    v.r8_quote({"quote": "天气很好我们出去", "evidence_type": "transcript", "subtitle_source": "ocr"}, verdict,
               quote_source="ocr")
    assert not verdict.hard
    assert any("跨句拼接" in m for m in verdict.corrected)


def test_r8_ocr_unmatched_is_soft_not_hard(make_validator, make_verdict):
    v = _validator()
    verdict = make_verdict(row_type="lib_quote")
    v.r8_quote({"quote": "这句话完全不在原料里出现", "evidence_type": "transcript", "subtitle_source": "ocr"}, verdict,
               quote_source="ocr")
    assert not verdict.hard
    assert any("未命中原料链" in m for m in verdict.soft)


def test_r8_ocr_anchor_mismatch_is_soft(make_validator, make_verdict):
    v = _validator()
    verdict = make_verdict(row_type="lib_quote")
    v.r8_quote({"quote": "天气很好", "evidence_type": "transcript", "subtitle_source": "ocr",
                "raw_sentence_id": "r2"}, verdict, quote_source="ocr")
    assert not verdict.hard
    assert any("回指不在覆盖区间" in m for m in verdict.soft)


def test_raw_source_of_priorities():
    v = _validator()
    # evidence_type 优先
    assert v._raw_source_of({"evidence_type": "ocr"}) == "ocr"
    # 无 evidence_type 时看 subtitle_source
    assert v._raw_source_of({"subtitle_source": "ocr"}) == "ocr"
    assert v._raw_source_of({"subtitle_source": "asr"}) == "transcript"
    # 均缺失 → transcript（口播严格链）
    assert v._raw_source_of({}) == "transcript"
