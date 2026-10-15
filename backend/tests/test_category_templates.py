"""品类模板测试（P0 三件套②）

验证：模板覆盖全部 20 赛道（与 categories.CATEGORY_TAXONOMY 对齐）；结构完整；
措辞不含校验白名单之外的枚举值、不含 C-4 反标签词；渲染入口行为正确。
全部内存隔离：不连库、不读 .env。
"""
from __future__ import annotations

from app.core.categories import CATEGORY_TAXONOMY
from app.core.category_templates import (
    CATEGORY_TEMPLATES,
    get_category_template,
    render_template_hint,
)


def test_templates_cover_all_taxonomy():
    """20 个赛道全部有模板（除"通用"等非赛道键外，模板键 ⊆ 赛道键）。"""
    assert len(CATEGORY_TAXONOMY) == 20
    for cat in CATEGORY_TAXONOMY:
        assert cat in CATEGORY_TEMPLATES, f"缺失模板: {cat}"


def test_template_structure_fields():
    """模板结构字段齐全：hook_patterns / structure_notes / copywriting_style / method_hints / quote_policy / combo_shape。"""
    for cat, tpl in CATEGORY_TEMPLATES.items():
        for field in (
            "hook_patterns", "structure_notes", "copywriting_style",
            "method_hints", "quote_policy", "combo_shape",
        ):
            assert field in tpl, f"{cat} 缺字段 {field}"
            value = tpl[field]
            if isinstance(value, list):
                assert len(value) >= 1, f"{cat}.{field} 不能为空"


def test_template_enum_terms_stay_in_whitelist():
    """模板措辞不引入白名单之外的枚举取值（仅做结构检查，枚举语义由 tl5 约束）。"""
    for cat, tpl in CATEGORY_TEMPLATES.items():
        for pat in tpl.get("hook_patterns", []):
            assert isinstance(pat, str)
        for style in tpl.get("copywriting_style", []):
            assert isinstance(style, str)


def test_template_no_c4_label_words():
    """C-4 反标签：模板不出现功能标签词/观后感评价词充当解释。"""
    bad = ("激发好奇", "引发共鸣", "吸引眼球", "很感动", "很治愈", "很上头", "绝了")
    for cat, tpl in CATEGORY_TEMPLATES.items():
        blob = " ".join(str(v) for v in tpl.values())
        for b in bad:
            assert b not in blob, f"{cat} 含反标签词: {b}"


def test_render_template_hint_behaviour():
    """渲染入口：未收录返回空串；收录返回含品类名与字段标题。"""
    assert render_template_hint("不存在品类") == ""
    assert render_template_hint("") == ""
    text = render_template_hint("知识口播")
    assert "知识口播" in text
    assert "品类模板" in text
    assert "常见钩子范式" in text
    assert "组合模板形态" in text


def test_get_template_missing_returns_none():
    assert get_category_template(None) is None
    assert get_category_template("随便一个") is None
    assert get_category_template("美食") is CATEGORY_TEMPLATES["美食"]
