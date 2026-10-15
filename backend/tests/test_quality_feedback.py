"""评估闭环测试（P0 三件套③）

验证：质量聚合纯函数口径与 validate.py 一致（<60 draft）；by_table 统计正确；
空输入安全。DB 侧聚合依赖真实会话，由运行期接口验证，此处只测纯逻辑。
全部内存隔离：不连库、不读 .env。
"""
from __future__ import annotations

from app.services.three_layer.quality_feedback import (
    DRAFT_THRESHOLD,
    summarize_lib_rows,
)


def test_summarize_empty_rows():
    out = summarize_lib_rows([])
    assert out["total"] == 0
    assert out["scored"] == 0
    assert out["draft"] == 0
    assert out["accepted"] == 0
    assert out["avg_score"] is None
    assert out["min_score"] is None
    assert out["by_table"] == {}


def test_summarize_basic_counts():
    rows = [
        {"table": "method", "quality_score": 85, "review_status": "accepted"},
        {"table": "method", "quality_score": 90, "review_status": "draft"},
        {"table": "hook", "quality_score": 40, "review_status": "draft"},
        {"table": "hook", "quality_score": None, "review_status": "draft"},
    ]
    out = summarize_lib_rows(rows)
    assert out["total"] == 4
    assert out["scored"] == 3
    assert out["draft"] == 1  # 仅 40 < 60
    assert out["accepted"] == 1
    assert out["avg_score"] == round((85 + 90 + 40) / 3, 1)
    assert out["min_score"] == 40
    assert out["by_table"]["method"]["count"] == 2
    assert out["by_table"]["method"]["draft"] == 0
    assert out["by_table"]["hook"]["count"] == 2
    assert out["by_table"]["hook"]["draft"] == 1


def test_threshold_matches_validate():
    """<60 判 draft，=60 不判（与 validate.py 口径一致）。"""
    rows_ok = [{"table": "topic", "quality_score": 60, "review_status": "draft"}]
    rows_bad = [{"table": "topic", "quality_score": 59.9, "review_status": "draft"}]
    assert DRAFT_THRESHOLD == 60
    assert summarize_lib_rows(rows_ok)["draft"] == 0
    assert summarize_lib_rows(rows_bad)["draft"] == 1


def test_summarize_avg_rounding():
    rows = [
        {"table": "a", "quality_score": 85, "review_status": "accepted"},
        {"table": "b", "quality_score": 84.5, "review_status": "draft"},
    ]
    out = summarize_lib_rows(rows)
    assert out["avg_score"] == round((85 + 84.5) / 2, 1)
