"""提示词契约完整性测试：三处契约内容、条款编号、层编码映射一致性。"""
from __future__ import annotations

import re

from app.services.prompt_contract import BREAKDOWN_OUTPUT_CONTRACT, CREATION_OUTPUT_CONTRACT
import app.services.three_layer.prompts as pkg
from app.services.three_layer.prompts.contract import (
    LAYER_ARRAY_KEYS,
    LAYER_CODES,
    LAYER_LABELS,
    THREE_LAYER_CONTRACT,
)


def _numbered_items(text: str) -> set[str]:
    return set(re.findall(r"^\s*(\d+)\.\s", text, re.M))


def test_three_layer_contract_has_all_clauses():
    clauses = set(re.findall(r"C-(\d+)", THREE_LAYER_CONTRACT))
    assert clauses == {str(i) for i in range(1, 9)}


def test_three_layer_contract_has_single_source():
    """体检 S3 修复：冗余模块 prompts.py 已删除，契约唯一权威源为 prompts/contract.py。"""
    import os

    assert pkg.__file__.endswith(os.path.join("prompts", "__init__.py"))
    legacy = os.path.normpath(os.path.join(os.path.dirname(pkg.__file__), os.pardir, "prompts.py"))
    assert not os.path.exists(legacy), "冗余模块 prompts.py 应已删除，契约应单一化到 prompts/contract.py"
    # 包 __init__ 必须原样 re-export 权威源，避免再次漂移
    assert pkg.THREE_LAYER_CONTRACT is THREE_LAYER_CONTRACT
    assert pkg.LAYER_CODES is LAYER_CODES
    assert pkg.LAYER_LABELS is LAYER_LABELS
    assert pkg.LAYER_ARRAY_KEYS is LAYER_ARRAY_KEYS


def test_breakdown_contract_has_11_clauses():
    items = _numbered_items(BREAKDOWN_OUTPUT_CONTRACT)
    assert items == {str(i) for i in range(1, 12)}
    for marker in ("简体中文", "完整闭合", "整数毫秒", "role_view", "时间锚点", "引用原文", "服务端校验"):
        assert marker in BREAKDOWN_OUTPUT_CONTRACT


def test_creation_contract_has_5_clauses():
    items = _numbered_items(CREATION_OUTPUT_CONTRACT)
    assert items == {str(i) for i in range(1, 6)}
    for marker in ("简体中文", "完整闭合", "可直接执行"):
        assert marker in CREATION_OUTPUT_CONTRACT


def test_layer_codes_single_source():
    """体检 S3 修复：LAYER_* 常量唯一来源为 prompts/contract.py，包仅 re-export。"""
    assert pkg.LAYER_CODES is LAYER_CODES
    assert pkg.LAYER_LABELS is LAYER_LABELS
    assert pkg.LAYER_ARRAY_KEYS is LAYER_ARRAY_KEYS


def test_layer_array_keys_covers_layer_codes():
    # L4.5 为整段脚本单对象（无数组字段），允许缺席
    assert set(LAYER_ARRAY_KEYS.keys()) <= set(LAYER_CODES.keys())
    assert set(LAYER_LABELS.keys()) == set(LAYER_CODES.keys())


def test_layer_array_keys_non_empty():
    for layer, keys in LAYER_ARRAY_KEYS.items():
        assert keys, f"L{layer} 缺数组字段"
        assert len(keys) == len(set(keys)), f"L{layer} 数组字段重复"
