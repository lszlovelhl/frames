"""ASR 原料清洗（raw.py 顶层纯函数）单元测试：错字修正 / 乱码检测 / 繁简。"""
from __future__ import annotations

from app.services.three_layer.raw import _clean_text, _is_garbled


def test_asr_fix_common_mistake():
    assert _clean_text("国境作业") == "国庆作业"


def test_asr_fix_marks_unclear_speech():
    out = _clean_text("俐落讯截")
    assert out.startswith("[语音不清]")


def test_garbled_detection_true():
    assert _is_garbled("俐落讯截") is True
    assert _is_garbled("这段内容端而不智完全不通") is True


def test_garbled_detection_false_for_normal():
    assert _is_garbled("今天天气很好我们一起去公园散步") is False


def test_garbled_detection_short_text():
    assert _is_garbled("好") is False


def test_clean_text_strips_and_preserves_clean():
    assert _clean_text("  正常的一句话  ") == "正常的一句话"
    assert _clean_text(None) == ""
    assert _clean_text("") == ""


def test_clean_text_simplifies_traditional_partially():
    # zh 转换表覆盖有限：常见字可转简，个别字（隻/貓）保留原文 → 已记入报告
    out = _clean_text("畫面中央出現一隻貓")
    assert "画面中央出现" in out


def test_traditional_detected():
    from app.services.zh import has_traditional

    assert has_traditional("畫面中央出現一隻貓") is True
    assert has_traditional("画面中央出现一只猫") is False
