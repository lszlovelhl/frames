"""纯逻辑单元测试：校验规则 R1~R15、文本工具、评分与元素基线。

覆盖 backend/app/services/three_layer/validate.py 的可测函数，
全部内存构造 Validator / Verdict，不碰数据库。
"""
from __future__ import annotations

import pytest

from app.services.three_layer.validate import (
    correct_time,
    element_baseline,
    is_code,
    is_label_only,
    is_shallow_imagination,
    is_shallow_mechanism,
    label_coverage,
    normalize_quote,
    score_rows,
    similarity,
)


# ---------------- 时间工具 ----------------

def test_correct_time_normal():
    assert correct_time(1000, 2000, None) == (1000, 2000, False)


def test_correct_time_end_zero_fills_from_start():
    assert correct_time(1000, 0, None) == (1000, 1000, True)


def test_correct_time_swap_when_end_before_start():
    assert correct_time(5000, 1000, None) == (1000, 5000, True)


def test_correct_time_source_overrides_when_far():
    # 与原料差 >300ms 时以原料为准
    assert correct_time(1000, 2000, (500, 500)) == (500, 500, True)


def test_correct_time_source_ignored_when_close():
    assert correct_time(1000, 2000, (900, 1900)) == (1000, 2000, False)


def test_correct_time_negative_clamped():
    assert correct_time(-5, -3, None) == (0, 0, True)


# ---------------- 编码 / 文本工具 ----------------

def test_is_code_valid():
    for code in ("tl5_lib.hook.a", "tl1_topline.x.y", "abc.def.ghi_1", "a.b.c"):
        assert is_code(code), code


def test_is_code_invalid():
    for code in ("tl5_lib", "a.b", "你好", "A.b.c", "abc.def.", "tl5 lib.hook.a"):
        assert not is_code(code), code


def test_normalize_quote_strips_punctuation():
    assert normalize_quote("剧情、多人、士兵模式") == "剧情多人士兵模式"
    assert normalize_quote("今天 天气！很好？") == "今天天气很好"


def test_similarity_identical_and_disjoint():
    assert similarity("完全相同的文本", "完全相同的文本") == 1.0
    assert similarity("abcdef", "xyz") < 0.5


def test_is_shallow_mechanism_short():
    assert is_shallow_mechanism("太短了") is True


def test_is_shallow_mechanism_long_substantive():
    text = "通过损失厌恶与信息缺口双重机制驱动：开头先制造一个无法立即得到答案的悬念，让用户带着未完成感持续观看，直到结尾用反差结论完成情绪释放，再以首因效应强化记忆锚点，整体形成完整的冲突—释放闭环结构，属于可复用的反常识冲突驱动方法论，换题材依然成立"
    assert len(text) >= 60
    assert is_shallow_mechanism(text) is False


def test_is_shallow_imagination_short():
    assert is_shallow_imagination("很震撼") is True


def test_label_coverage_and_is_label_only():
    assert label_coverage("钩子铺垫") >= 0.9
    assert is_label_only("钩子") is True
    assert is_label_only("钩子铺垫转折高潮") is True
    assert is_label_only("这里用钩子是因为观众需要悬念来留住注意力") is False


# ---------------- R1 必填 ----------------

def test_r1_required_missing_field(make_validator, make_verdict):
    v = make_validator()
    row = {"name": "x", "category": "叙事", "mechanism": "", "usage_steps": "长内容", "counter_example": "无", "variants": [], "imagination": ""}
    verdict = make_verdict(row_type="lib_method")
    v.r1_required("lib_method", row, verdict)
    assert any(m.startswith("R1") and "mechanism" in m for m in verdict.hard)
    assert any(m.startswith("R1") and "variants" in m for m in verdict.hard)


def test_r1_required_complete_row_no_hard(make_validator, make_verdict):
    v = make_validator()
    row = {
        "name": "x", "category": "叙事", "mechanism": "利用反常识冲突", "usage_steps": "先A再B",
        "counter_example": "无", "variants": [{"text": "a"}, {"text": "b"}], "imagination": "想象内容想象内容想象内容想象内容",
    }
    verdict = make_verdict(row_type="lib_method")
    v.r1_required("lib_method", row, verdict)
    assert not [m for m in verdict.hard if m.startswith("R1")]


# ---------------- R2 枚举 ----------------

def test_r2_enum_out_of_whitelist(make_validator, make_verdict):
    v = make_validator()
    row = {"intent": "乱写", "emotion_shape": "单峰"}
    verdict = make_verdict(row_type="lib_combo")
    v.r2_enum("lib_combo", row, verdict)
    assert any(m.startswith("R2") and "intent" in m for m in verdict.hard)


def test_r2_enum_in_whitelist(make_validator, make_verdict):
    v = make_validator()
    row = {"intent": "涨粉", "emotion_shape": "单峰"}
    verdict = make_verdict(row_type="lib_combo")
    v.r2_enum("lib_combo", row, verdict)
    assert not [m for m in verdict.hard if m.startswith("R2")]


# ---------------- R3 时间 ----------------

def test_r3_time_swapped_end_before_start_corrected(make_validator, make_verdict):
    # R3 先经 correct_time 自洽化：end<start 会被交换并记录修正，不产生「不自洽」hard
    v = make_validator()
    verdict = make_verdict(row_type="script_turn_point")
    v.r3_time({"start_ms": 5000, "end_ms": 1000}, verdict)
    assert not [m for m in verdict.hard if m.startswith("R3") and "不自洽" in m]
    assert any(m.startswith("R3") and "修正" in m for m in verdict.corrected)


def test_r3_time_out_of_bounds(make_validator, make_verdict):
    v = make_validator(duration_ms=60_000)
    verdict = make_verdict(row_type="script_turn_point")
    v.r3_time({"start_ms": 1_000, "end_ms": 70_000}, verdict)
    assert any(m.startswith("R3") and "越界" in m for m in verdict.soft)


def test_r3_time_corrected_from_source(make_validator, make_verdict):
    v = make_validator()
    verdict = make_verdict(row_type="script_turn_point")
    v.r3_time({"start_ms": 1000, "end_ms": 9000}, verdict, source=(500, 500))
    assert any(m.startswith("R3") and "修正" in m for m in verdict.corrected)


# ---------------- R4 粒度 / R5 编码 ----------------

def test_r4_granularity_out_of_range(make_validator, make_verdict):
    v = make_validator(duration_ms=60_000)  # 期望 [15, 25]
    verdict = make_verdict(row_type="script_segment")
    v.r4_granularity(10, verdict)
    assert any(m.startswith("R4") for m in verdict.soft)


def test_r4_granularity_in_range(make_validator, make_verdict):
    v = make_validator(duration_ms=60_000)
    verdict = make_verdict(row_type="script_segment")
    v.r4_granularity(20, verdict)
    assert not [m for m in verdict.soft if m.startswith("R4")]


def test_r5_code_invalid(make_validator, make_verdict):
    v = make_validator()
    verdict = make_verdict(row_type="lib_topic")
    v.r5_code({"code": "tl5_lib.hook"}, verdict)
    assert any(m.startswith("R5") for m in verdict.hard)


def test_r5_code_valid(make_validator, make_verdict):
    v = make_validator()
    verdict = make_verdict(row_type="lib_topic")
    v.r5_code({"code": "tl5_lib.hook.a"}, verdict)
    assert not [m for m in verdict.hard if m.startswith("R5")]


# ---------------- R6 三件套 / R7 原料回指 ----------------

def test_r6_triplet_missing(make_validator, make_verdict):
    v = make_validator()
    verdict = make_verdict(row_type="lib_quote")
    v.r6_triplet({"quote": "原文"}, verdict)
    assert any(m.startswith("R6") for m in verdict.hard)


def test_r7_raw_ref_invalid(make_validator, make_verdict):
    v = make_validator(raw_by_id={"r1": {"id": "r1", "text": "原句"}})
    verdict = make_verdict(row_type="lib_quote")
    v.r7_raw_ref({"raw_sentence_id": "r2"}, verdict)
    assert any(m.startswith("R7") for m in verdict.hard)


def test_r7_raw_ref_valid(make_validator, make_verdict):
    v = make_validator(raw_by_id={"r1": {"id": "r1", "text": "原句"}})
    verdict = make_verdict(row_type="lib_quote")
    v.r7_raw_ref({"raw_sentence_id": "r1"}, verdict)
    assert not [m for m in verdict.hard if m.startswith("R7")]


# ---------------- R10 画面照搬 ----------------

def test_r10_scene_copy_detected(make_validator, make_verdict):
    v = make_validator(shot_descs=["画面中央出现一只猫", "背景是蓝天草地"])
    verdict = make_verdict(row_type="lib_method")
    v.r10_scene_copy({"name": "画面中央出现一只猫", "mechanism": "", "usage_steps": "", "function_reason": "", "purpose": "", "expected_function": ""}, verdict)
    assert any(m.startswith("R10") for m in verdict.soft)


def test_r10_scene_copy_clean(make_validator, make_verdict):
    v = make_validator(shot_descs=["画面中央出现一只猫", "背景是蓝天草地"])
    verdict = make_verdict(row_type="lib_method")
    v.r10_scene_copy({"name": "利用损失厌恶制造悬念", "mechanism": "", "usage_steps": "", "function_reason": "", "purpose": "", "expected_function": ""}, verdict)
    assert not [m for m in verdict.soft if m.startswith("R10")]


# ---------------- R11 功能标签 / 观后感 ----------------

def test_r11_label_only_hard(make_validator, make_verdict):
    v = make_validator()
    verdict = make_verdict(row_type="lib_method")
    v.r11_label({"function_reason": "钩子铺垫转折高潮"}, verdict)
    assert any(m.startswith("R11") and "功能标签" in m for m in verdict.hard)


def test_r11_feeling_word_soft(make_validator, make_verdict):
    v = make_validator()
    verdict = make_verdict(row_type="lib_method")
    v.r11_label({"function_reason": "这个开头很抓人", "mechanism": "", "usage_steps": ""}, verdict)
    assert any(m.startswith("R11") and "观后感" in m for m in verdict.soft)


def test_r11_clean(make_validator, make_verdict):
    v = make_validator()
    verdict = make_verdict(row_type="lib_method")
    v.r11_label({"function_reason": "因为前3秒抛出冲突能留住观众注意力", "mechanism": "", "usage_steps": ""}, verdict)
    assert not [m for m in verdict.hard + verdict.soft if m.startswith("R11")]


# ---------------- R12 发散 / R13 机制 / R15 深度 ----------------

def test_r12_divergence_too_few(make_validator, make_verdict):
    v = make_validator()
    verdict = make_verdict(row_type="lib_method")
    v.r12_divergence({"variants": [], "imagination": "足够长的想象内容足够长的想象内容足够长的想象内容"}, verdict)
    assert any(m.startswith("R12") and "变体不足" in m for m in verdict.soft)


def test_r12_divergence_duplicate(make_validator, make_verdict):
    v = make_validator()
    verdict = make_verdict(row_type="lib_method")
    v.r12_divergence({"variants": [{"text": "同一个方法"}, {"text": "同一个方法"}], "imagination": "足够长的想象内容足够长的想象内容足够长的想象内容"}, verdict)
    assert any(m.startswith("R12") and "重复" in m for m in verdict.soft)


def test_r12_divergence_short_imagination(make_validator, make_verdict):
    v = make_validator()
    verdict = make_verdict(row_type="lib_method")
    v.r12_divergence({"variants": [{"text": "换成教程类题材也能用"}, {"text": "换成剧情类题材也能用"}], "imagination": "太短"}, verdict)
    assert any(m.startswith("R12") and "发散不足" in m for m in verdict.soft)


def test_r12_divergence_clean(make_validator, make_verdict):
    v = make_validator()
    verdict = make_verdict(row_type="lib_method")
    v.r12_divergence(
        {
            "variants": [{"text": "教程类题材可换成产品拆解"}, {"text": "剧情类题材可换成人物访谈"}],
            "imagination": "把这个方法迁移到口播科普类视频，用开场悬念引出结论，再逐层展开证据链，最后回扣反常识点",
        },
        verdict,
    )
    assert not [m for m in verdict.soft if m.startswith("R12")]


def test_r13_mechanism_short(make_validator, make_verdict):
    v = make_validator()
    verdict = make_verdict(row_type="lib_method")
    v.r13_mechanism({"usage_steps": "短", "counter_example": "有"}, verdict)
    assert any(m.startswith("R13") and "过短" in m for m in verdict.soft)


def test_r13_mechanism_missing_verb(make_validator, make_verdict):
    v = make_validator()
    verdict = make_verdict(row_type="lib_method")
    steps = "这一段内容非常详细地描述了整个流程的执行方式，但是通篇没有出现任何明确的动作引导词"
    v.r13_mechanism({"usage_steps": steps, "counter_example": "有"}, verdict)
    assert any(m.startswith("R13") and "动词" in m for m in verdict.soft)


def test_r13_mechanism_missing_counter_example(make_validator, make_verdict):
    v = make_validator()
    verdict = make_verdict(row_type="lib_method")
    v.r13_mechanism({"usage_steps": "先用钩子开场，再把冲突前置，然后切换视角补足悬念，最后用反常识收尾", "counter_example": ""}, verdict)
    assert any(m.startswith("R13") and "反例" in m for m in verdict.soft)


def test_r15_depth_shallow(make_validator, make_verdict):
    v = make_validator()
    verdict = make_verdict(row_type="lib_method")
    v.r15_depth({"mechanism": "很好用", "imagination": "很震撼"}, verdict)
    assert any(m.startswith("R15") and "机制单薄" in m for m in verdict.soft)
    assert any(m.startswith("R15") and "想象单薄" in m for m in verdict.soft)


def test_r15_depth_clean(make_validator, make_verdict):
    v = make_validator()
    verdict = make_verdict(row_type="lib_method")
    v.r15_depth(
        {
            "mechanism": "通过损失厌恶与信息缺口双重机制驱动：开头先制造一个无法立即得到答案的悬念，让用户带着未完成感持续观看，直到结尾用反差结论完成情绪释放，再以首因效应强化记忆锚点，整体形成完整的冲突—释放闭环结构，属于可复用的反常识冲突驱动方法论，换题材依然成立",
            "imagination": "把开场悬念与损失厌恶的冲突结构平移到财经解读类口播里，先抛出反常识结论制造信息缺口，再逐层给证据，最后用反差收尾，让用户形成追看习惯并愿意转发，完成从短到长的题材迁移验证",
        },
        verdict,
    )
    assert not [m for m in verdict.soft if m.startswith("R15")]


# ---------------- 评分 / 元素基线 ----------------

def test_quality_score_and_status(make_verdict):
    v = make_verdict()
    assert v.quality_score == 100.0
    assert v.status == "active"
    v.soft.append("R12 变体不足")
    v.soft.append("R13 操作步骤过短")
    assert v.quality_score == 70.0
    assert v.status == "active"  # 2 条 soft → 70 分仍在及格线
    v.soft.append("R15 机制单薄")
    assert v.quality_score == 55.0
    assert v.status == "draft"   # 3 条 soft → 55 分跌入草稿
    v.hard.append("R8 原话一致度不足")
    assert v.accepted is False
    assert v.status == "rejected"


def test_score_rows_summary(make_verdict):
    good = make_verdict()
    rejected = make_verdict()
    rejected.hard.append("R8 原话一致度不足")
    draft = make_verdict()
    draft.soft.extend(["R12 变体不足", "R13 操作步骤过短", "R15 机制单薄"])
    summary = score_rows([good, rejected, draft])
    assert summary["total"] == 3
    assert summary["rejected"] == 1
    assert summary["draft"] == 1
    assert summary["active"] == 1
    assert summary["soft_total"] == 3


def test_element_baseline_pass():
    verdicts = [
        {"row_type": "lib_method", "label": "a", "hard": [], "soft": []},
        {"row_type": "lib_method", "label": "b", "hard": [], "soft": []},
        {"row_type": "script_sentence", "label": "c", "hard": [], "soft": []},  # 不计入
    ]
    result = element_baseline(verdicts)
    assert result["pass"] is True
    assert result["total"] == 2
    assert result["rates"]["hard"] == 0.0


def test_element_baseline_fail_on_hard():
    verdicts = [
        {"row_type": "lib_method", "label": "a", "hard": ["R5 编码不规范"], "soft": []},
    ]
    result = element_baseline(verdicts)
    assert result["pass"] is False
    assert len(result["hard_rows"]) == 1


def test_element_baseline_no_lib_rows():
    verdicts = [{"row_type": "script_sentence", "label": "c", "hard": [], "soft": []}]
    result = element_baseline(verdicts)
    assert result["pass"] is False
    assert "无元素产出" in result["reason"]
