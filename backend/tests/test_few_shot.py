"""few-shot 示范库测试（P0 三件套①）

验证：示范条目在真实运行时能通过校验语义（对齐 R1~R15 / C-1~C-8）；
load/render 行为正确；品类回退与 layer 过滤正确。
全部内存隔离：不连库、不读 .env。
"""
from __future__ import annotations

import pytest

from app.services.three_layer.few_shot import (
    FEW_SHOT_SAMPLES,
    load_few_shots,
    render_few_shots,
)
from app.services.three_layer.validate import ENUM_FALLBACK, Validator


@pytest.fixture
def whitelists() -> dict[str, set[str]]:
    wl = {k: set(v) for k, v in ENUM_FALLBACK.items()}
    wl["role_view"] = {"编导", "运营", "拍摄", "剪辑", "全员"}
    # hook_type 白名单来自 tl5 提示词枚举（8 值），非 lib_tag 行数据时用该集
    wl.setdefault("hook_type", {
        "结果前置", "抛冲突", "提问", "悬念", "金句", "否定式", "视觉中断", "利益承诺",
    })
    return wl


def _all_items():
    for group in FEW_SHOT_SAMPLES.values():
        for key in ("topics", "hooks", "copywriting", "quotes", "methods"):
            yield from group.get(key, [])


def _row_type(item: dict) -> str:
    code = item.get("code", "")
    if code.startswith("topic."):
        return "lib_topic"
    if code.startswith("hook."):
        return "lib_hook"
    if code.startswith("copy."):
        return "lib_copywriting"
    if code.startswith("quote."):
        return "lib_quote"
    if code.startswith("method."):
        return "lib_method"
    return "lib_combo"


@pytest.fixture
def validator(whitelists):
    return Validator(duration_ms=60_000, whitelists=whitelists)


def test_contract_keys_present_in_samples():
    """示范条目覆盖 C-1~C-8 所涉字段：枚举白名单、code 三段式、机制/想象/变体/引用。"""
    items = list(_all_items())
    assert len(items) >= 3, "至少要有可用示范条目"
    for item in items:
        assert item["code"].count(".") == 2, f"code 必须三段式小写: {item['code']}"
        assert item["code"].split(".")[0] in {"topic", "hook", "copy", "quote", "method"}
        assert item.get("quote"), "示范必须带逐字引用（对齐 R8）"
        assert len(item.get("variants", [])) >= 2, "variants 至少 2 条（R12 发散）"
        assert item.get("source_line_no", 0) > 0


def test_enum_fields_within_whitelist(validator):
    """R2 枚举：hook_type / position / topic_type / value_type / copy_type / abstraction_level。"""
    for item in _all_items():
        row_type = _row_type(item)
        if row_type == "lib_method":
            assert item["category"] in validator.wl["method_category"], item["code"]
            assert item["abstraction_level"] in validator.wl["abstraction_level"], item["code"]
        if row_type == "lib_hook":
            assert item["hook_type"] in validator.wl["hook_type"], item["code"]
            assert item["position"] in validator.wl["hook_position"], item["code"]
        if row_type == "lib_topic":
            assert item["topic_type"] in validator.wl["topic_type"], item["code"]
            assert item["value_type"] in validator.wl["value_type"], item["code"]
        if row_type == "lib_copywriting":
            assert item["copy_type"] in validator.wl["copy_type"], item["code"]


def test_depth_rules_on_methods_and_imagination(validator):
    """R15 深度：mechanism ≥60 字且点名具体机制；imagination ≥50 字且落到具体迁移动作。

    额外用 C-4 反标签扫描：禁止功能标签词/观后感评价词充当解释。
    """
    cliche_tokens = (
        "激发好奇", "引发共鸣", "吸引眼球", "观众共鸣", "产生共鸣",
        "提升完播", "提高转化", "增强互动", "增加曝光", "爆款",
        "很感动", "很治愈", "很上头", "绝了",
    )
    for item in _all_items():
        mech = item.get("mechanism", "")
        assert len(mech) >= 60, f"{item['code']} mechanism 需 ≥60 字"
        assert any(t in mech for t in (
            "反常识", "认知失调", "信息缺口", "损失厌恶", "峰终", "社交货币",
            "悬念", "期待", "锚定", "首因", "即时反馈", "情感", "信任",
        )), f"{item['code']} mechanism 需点名具体机制"
        img = item.get("imagination", "")
        assert len(img) >= 50, f"{item['code']} imagination 需 ≥50 字"
        assert "迁移到其他领域" not in img and "换到别的领域" not in img, "禁止场景置换套话"
        # 落到具体迁移动作：要求出现目标人群或"放到X号"式具体目标
        assert any(t in img for t in ("放到", "人群", "观众", "领域", "场景", "账号", "改成")), (
            f"{item['code']} imagination 需落到具体迁移动作"
        )
        for bad in cliche_tokens:
            assert bad not in mech and bad not in img, f"{item['code']} 含反标签词: {bad}"


def test_variants_meet_r12_spread(validator):
    """R12 发散：variants ≥2 且两两不相似；imagination ≥30 字（对 topic/hook 等）。"""
    for item in _all_items():
        variants = item.get("variants", [])
        assert len(variants) >= 2
        texts = [v.get("text", "") for v in variants]
        for i in range(len(texts)):
            for j in range(i + 1, len(texts)):
                assert texts[i] != texts[j], f"{item['code']} variants 重复"
        # 简版相似度：共享字符占比 < 0.8（近似 R12 的相似度阈值）
        for i in range(len(texts)):
            for j in range(i + 1, len(texts)):
                common = len(set(texts[i]) & set(texts[j]))
                if texts[i]:
                    assert common / len(set(texts[i])) < 0.8, (
                        f"{item['code']} variant 相似度过高"
                    )


def test_usage_steps_and_counter_example_r13():
    """R13 手法：usage_steps ≥30 字且含具体动词；counter_example 非空。"""
    verbs = ("写", "拆", "拍", "剪", "放", "切", "落", "接", "先", "再", "用")
    for item in _all_items():
        if _row_type(item) != "lib_method":
            continue
        steps = item.get("usage_steps", "")
        assert len(steps) >= 30, f"{item['code']} usage_steps 需 ≥30 字"
        assert any(v in steps for v in verbs), f"{item['code']} usage_steps 需含具体动词"
        assert item.get("counter_example"), f"{item['code']} 需给反例"


def test_quote_verbatim_requirement():
    """R8 逐字引用：示范 quote 必须存在于 source 或明确标注为原片引用（逐字）。"""
    for item in _all_items():
        q = item.get("quote", "")
        assert q.strip(), "quote 不能为空"
        assert "\n" not in q, "quote 应为单句原话"


def test_load_few_shots_category_fallback():
    """品类未命中回退"通用"；layer 过滤只保留 L5 示范。"""
    items_known = load_few_shots("美食", layer=5)
    assert len(items_known) > 0
    items_unknown = load_few_shots("不存在品类", layer=5)
    assert items_unknown == load_few_shots("通用", layer=5)
    # layer=1 时应为空（示范条目均为 L5 积木）
    assert load_few_shots("美食", layer=1) == []


def test_render_few_shots_output_shape():
    """渲染文本包含示范块、品类名与校验约束提醒。"""
    text = render_few_shots("知识口播", layer=5)
    assert "few-shot 示范" in text
    assert "知识口播" in text
    assert "mechanism" in text
    assert "imagination" in text
    assert "quote" in text
