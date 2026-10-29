"""七维结构化映射推导测试（dimensions.py，离线）。

证明七维（钩子/节奏/情绪/画面/结构/完播驱动/可复用元素）都能从结构化
dict 数据确定性推导出来。
"""
from __future__ import annotations

from app.services.three_layer.completion import derive_completion_drivers
from app.services.three_layer.dimensions import (
    DIMENSION_KEYS,
    STATUS_MISSING,
    STATUS_OK,
    STATUS_WEAK,
    assemble_dimensions,
)
from app.services.three_layer.rhythm import derive_rhythm
from fixtures.sample_data import (
    DURATION_MS,
    SAMPLE_CURVE_OK,
    SAMPLE_LIB_COUNTS_OK,
    SAMPLE_SEGMENTS,
    SAMPLE_SENTENCES,
    SAMPLE_SHOTS,
)


def _good_kwargs():
    rhythm = derive_rhythm(SAMPLE_SEGMENTS, SAMPLE_SENTENCES, duration_ms=DURATION_MS)
    drivers = derive_completion_drivers(
        SAMPLE_SEGMENTS, SAMPLE_SENTENCES, duration_ms=DURATION_MS
    )
    return dict(
        core_idea="合成样本核心思想：开场钩子 + 密度节奏 + 高潮兑现",
        curve=SAMPLE_CURVE_OK,
        rhythm=rhythm,
        drivers=drivers,
        segments=SAMPLE_SEGMENTS,
        sentences=SAMPLE_SENTENCES,
        lib_counts=SAMPLE_LIB_COUNTS_OK,
        element_baseline={"pass": True, "score": 82, "reason": ""},
        shots_n=len(SAMPLE_SHOTS),
        frame_evidence_n=3,
    )


def test_all_seven_dimensions_present():
    out = assemble_dimensions(**_good_kwargs())
    assert list(out["dimensions"].keys()) == list(DIMENSION_KEYS)
    assert out["labels"]["hook_open"].startswith("①")
    assert out["labels"]["reusable_elements"].startswith("⑦")


def test_good_sample_has_no_missing_dimension():
    out = assemble_dimensions(**_good_kwargs())
    assert out["status_count"][STATUS_MISSING] == 0
    assert out["all_ok"] is True


def test_dimension_statuses_in_known_set():
    out = assemble_dimensions(**_good_kwargs())
    for dim in out["dimensions"].values():
        assert dim["status"] in (STATUS_OK, STATUS_WEAK, STATUS_MISSING)
        assert dim["source"]
        assert isinstance(dim["count"], int)


def test_empty_inputs_produce_missing_statuses():
    out = assemble_dimensions(
        core_idea="",
        curve=None,
        rhythm=None,
        drivers=[],
        segments=[],
        sentences=[],
        lib_counts={},
        element_baseline=None,
        shots_n=0,
        frame_evidence_n=0,
    )
    dims = out["dimensions"]
    assert dims["hook_open"]["status"] == STATUS_MISSING
    assert dims["rhythm_curve"]["status"] == STATUS_MISSING
    assert dims["emotion_curve"]["status"] == STATUS_MISSING
    assert dims["visual_language"]["status"] == STATUS_MISSING
    assert dims["copy_structure"]["status"] == STATUS_MISSING
    assert dims["completion_drivers"]["status"] == STATUS_MISSING
    assert dims["reusable_elements"]["status"] == STATUS_MISSING
    assert out["status_count"][STATUS_MISSING] == 7


def test_partial_data_yields_weak_status():
    # 只有段落、没有句子、没有积木：结构维薄弱，其余缺失
    rhythm = derive_rhythm(SAMPLE_SEGMENTS, [], duration_ms=DURATION_MS)
    out = assemble_dimensions(
        core_idea="有一段核心思想但内容单薄",
        curve=None,
        rhythm=rhythm,
        drivers=[],
        segments=SAMPLE_SEGMENTS,
        sentences=[],
        lib_counts={},
        element_baseline=None,
        shots_n=0,
        frame_evidence_n=0,
    )
    assert out["dimensions"]["copy_structure"]["status"] in (STATUS_WEAK, STATUS_MISSING)
    assert out["dimensions"]["visual_language"]["status"] == STATUS_MISSING


def test_visual_dimension_weak_when_only_shots_no_evidence():
    out = assemble_dimensions(
        **{**_good_kwargs(), "frame_evidence_n": 0}
    )
    assert out["dimensions"]["visual_language"]["status"] == STATUS_WEAK
