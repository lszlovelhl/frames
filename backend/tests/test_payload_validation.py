"""LLM 输出结构校验纯函数测试（validate_layer_payload，离线、无 DB/网络）。

覆盖：空对象、必填缺失、空数组、枚举越界、强度越界五类故障信号，
这些信号会被 common._run_layer 回喂模型做有界反馈重试。
"""
from __future__ import annotations

from app.services.three_layer.stages import validate_layer_payload


def test_valid_layer3_payload_has_no_issues():
    data = {
        "segments": [
            {"seq": 1, "seg_type": "钩子", "start_ms": 0, "end_ms": 5000},
            {"seq": 2, "seg_type": "干货", "start_ms": 5000, "end_ms": 12000},
        ]
    }
    assert validate_layer_payload(3, data, ["segments"]) == []


def test_empty_segments_array_flagged():
    issues = validate_layer_payload(3, {"segments": []}, ["segments"])
    assert any("segments" in i and "空" in i for i in issues)


def test_seg_type_enum_out_of_range_flagged():
    data = {"segments": [{"seq": 1, "seg_type": "外星生物入侵段"}]}
    issues = validate_layer_payload(3, data, ["segments"])
    assert any("seg_type 枚举越界" in i for i in issues)


def test_layer1_missing_required_scalar_flagged():
    issues = validate_layer_payload(1, {"core_idea": ""}, ["key_facts"])
    assert any("core_idea" in i for i in issues)
    # key_facts 可选，不应报缺失
    assert not any("key_facts" in i for i in issues)


def test_layer1_missing_multiple_fields_collected():
    issues = validate_layer_payload(1, {}, ["key_facts"])
    keys = "；".join(issues)
    for field in ("core_idea", "content_trend", "target_audience", "summary"):
        assert field in keys


def test_layer4_emotion_out_of_range_and_bad_function():
    data = {
        "sentences": [
            {"seq": 1, "sentence_function": "不存在的功能名", "emotion_intensity": 11},
        ]
    }
    issues = validate_layer_payload(4, data, ["sentences"])
    assert any("sentence_function 枚举越界" in i for i in issues)
    assert any("emotion_intensity" in i for i in issues)


def test_layer4_valid_intensity_passes():
    data = {"sentences": [{"seq": 1, "sentence_function": "钩子", "emotion_intensity": 7}]}
    assert validate_layer_payload(4, data, ["sentences"]) == []


def test_layer6_bad_slot_role_flagged():
    data = {"combo": {"name": "x"}, "slots": [{"slot_role": "法师位"}]}
    issues = validate_layer_payload(6, data, ["slots"])
    assert any("slot_role 枚举越界" in i for i in issues)


def test_non_dict_payload_reported_immediately():
    assert validate_layer_payload(3, None, ["segments"])  # type: ignore[arg-type]
    assert validate_layer_payload(3, ["not", "a", "dict"], ["segments"])  # type: ignore[arg-type]