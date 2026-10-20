"""三层流水线拆解阶段模块结构与协调器单元测试。"""
import pytest
from app.services.three_layer.pipeline import (
    Progress,
    _PipelineAbort,
    _abort_cleanup,
    _classify_route,
    purge_three_layer,
    run_three_layer,
)
from app.services.three_layer.stages import (
    _clamp_intensity,
    _fmt_transcript,
    _looks_lyric,
    run_stage1_anchor,
    run_stage2_rhythm,
    run_stage3_structure,
    run_stage4_refine,
    run_stage5_elements,
)


def test_stages_import_and_exports():
    """验证各阶段 runner 及公共工具导出完整无损。"""
    assert callable(run_three_layer)
    assert callable(purge_three_layer)
    assert callable(_abort_cleanup)
    assert callable(run_stage1_anchor)
    assert callable(run_stage2_rhythm)
    assert callable(run_stage3_structure)
    assert callable(run_stage4_refine)
    assert callable(run_stage5_elements)


def test_clamp_intensity():
    """验证强度归一化区间 [0, 10]。"""
    assert _clamp_intensity(None) == 0.0
    assert _clamp_intensity("invalid") == 0.0
    assert _clamp_intensity(-2) == 0.0
    assert _clamp_intensity(15) == 10.0
    assert _clamp_intensity(7.5) == 7.5


def test_looks_lyric():
    """验证歌词特征识别。"""
    assert _looks_lyric("Baby give me one more chance tonight") is True
    assert _looks_lyric("今天我们来聊聊短视频拆解的三个底层逻辑") is False


def test_fmt_transcript():
    """验证转写行格式化包含时间戳与行号。"""
    sentences = [
        {"seq": 1, "start_ms": 1000, "end_ms": 3500, "text": "第一句话"},
        {"seq": 2, "start_ms": 4000, "end_ms": 6200, "text": "第二句话"},
    ]
    txt = _fmt_transcript(sentences, with_ms=True)
    assert "#01 [1000-3500ms] 第一句话" in txt
    assert "#02 [4000-6200ms] 第二句话" in txt


def _u(source: str) -> dict:
    return {"source": source, "text": "x" * 10}


def test_route_classify_bgm():
    """BGM/氛围型：口播≤1 + OCR≤2。"""
    r = _classify_route(
        asr_count=0, units=[_u("ocr")], duration_ms=30_000, shots=[]
    )
    assert r["vtype"] == "bgm"
    assert r["length"] == "short"
    assert r["compact_mode"] is False
    assert any("BGM" in n for n in r["notes"])


def test_route_classify_talking_head():
    """口播型：高密度口播。"""
    r = _classify_route(
        asr_count=30, units=[_u("asr")] * 30, duration_ms=120_000, shots=[]
    )
    assert r["vtype"] == "talking_head"
    assert r["length"] == "medium"
    assert r["compact_mode"] is False


def test_route_classify_mixed_long():
    """混合型长视频：触发 compact_mode。"""
    r = _classify_route(
        asr_count=8, units=[_u("asr")] * 8, duration_ms=420_000, shots=[{}] * 5
    )
    assert r["vtype"] == "mixed"
    assert r["length"] == "long"
    assert r["compact_mode"] is True
    assert r["shots_n"] == 5
    assert any("长视频" in n for n in r["notes"])
