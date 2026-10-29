"""breakdown_index 纯逻辑离线单测。

覆盖（不连真实库、不打网络、不调 Ollama）：
- 六类分块组装器齐全且产出稳定字段；
- content_hash 幂等（文本未变指纹不变，变了则变）；
- 混合融合：hybrid 权重 0.7 向量 + 0.3 关键词；向量缺失降级纯 BM25；
- owner_key / kind 过滤；空候选与空 query 结果为空不报错。

DB 交互薄封装（collect_chunks SQL、index_breakdown 写入、search_chunks 取数）
依赖真实表结构，不在本文件覆盖；embed 调用点由 index 流程之外的测试环境 monkeypatch。
"""
from __future__ import annotations

import pytest

from app.services import breakdown_index as bi
from app.services.memory_search import bm25_score


def _cand(cid: str, content: str, *, kind: str = "segment", owner: str = "default") -> dict:
    return {
        "id": cid,
        "owner_key": owner,
        "chunk_kind": kind,
        "content": content,
        "title": "标题",
        "video_id": "v1",
        "script_id": "s1",
        "ref_table": "script_segment",
        "ref_id": "r1",
    }


# --------------------------------------------------------------- 1. 分块组装
def test_all_six_kinds_have_assembler():
    """六类 kind 均有组装器且 chunk_kind 正确落值。"""
    producers = {
        bi.CHUNK_SUMMARY: bi.build_summary_chunk({"id": "s1"}),
        bi.CHUNK_SEGMENT: bi.build_segment_chunk({"id": "g1", "seq": 1}),
        bi.CHUNK_HOOK: bi.build_segment_hook_chunk({"id": "g2"}),
        bi.CHUNK_SENTENCE: bi.build_sentence_chunk({"id": "t1"}),
        bi.CHUNK_DRIVER: bi.build_driver_chunk({"driver": "d1"}, 0),
        bi.CHUNK_ELEMENT: bi.build_element_chunk("lib_method", {"id": "e1"}),
    }
    for kind in bi.CHUNK_KINDS:
        assert producers[kind]["chunk_kind"] == kind
        assert producers[kind]["content"]  # 组装文本非空


def test_summary_chunk_joins_core_fields():
    ch = bi.build_summary_chunk(
        {
            "id": "s1",
            "core_idea": "焦虑开场讲收纳",
            "content_trend": "痛点—方法—总结",
            "target_audience": "新手宝妈",
            "summary": "三段式讲清三个动作",
            "quality_score": 8.2,
        },
        video_title="家居收纳爆款",
    )
    assert ch["ref_table"] == "script_script"
    assert ch["seq"] == 0
    for token in ("家居收纳爆款", "焦虑开场讲收纳", "痛点—方法—总结", "新手宝妈", "8.2"):
        assert token in ch["content"], token


def test_segment_chunk_carries_time_ratio():
    seg = {
        "id": "g1", "seq": 2, "seg_type": "钩子", "title": "开头留人",
        "start_ms": 0, "end_ms": 15000, "purpose": "3 秒抛痛点", "summary": "制造共鸣",
    }
    ch = bi.build_segment_chunk(seg, duration_ms=60_000)
    assert ch["chunk_kind"] == bi.CHUNK_SEGMENT
    assert "25%" in ch["content"]          # 15s / 60s ≈ 25%
    assert "00:00–00:15" in ch["content"]
    assert ch["title"].startswith("第2段")


def test_hook_chunks_two_sources_present():
    seg_hook = bi.build_segment_hook_chunk(
        {"id": "g1", "seq": 1, "seg_type": "钩子", "title": "痛点前置",
         "purpose": "留人", "summary": "先抛问题", "start_ms": 0, "end_ms": 5000}
    )
    lib_hook = bi.build_hook_library_chunk(
        {"id": "h1", "name": "反问开场", "hook_type": "反问",
         "sentence_pattern": "你是不是也…", "expected_effect": "停留", "mechanism": "降低防御"}
    )
    assert seg_hook["ref_table"] == "script_segment"
    assert lib_hook["ref_table"] == "lib_hook"
    assert "钩子" in seg_hook["content"] and "反问开场" in lib_hook["content"]


def test_sentence_chunk_exposes_quote_and_role():
    ch = bi.build_sentence_chunk(
        {
            "id": "t1", "seq": 3, "quote": "你是不是每天收拾完又乱了",
            "sentence_function": "钩子", "function_reason": "第二句承接痛点",
            "start_ms": 8000, "end_ms": 11000, "emotion_intensity": 7.5,
        }
    )
    assert ch["chunk_kind"] == bi.CHUNK_SENTENCE
    assert "你是不是每天收拾完又乱了" in ch["content"]
    assert "第二句承接痛点" in ch["content"]
    assert "00:08" in ch["content"]


def test_driver_chunk_ref_id_stable():
    driver = {
        "driver": "hook_open", "label": "开场钩子", "evidence_quote": "第一句反问",
        "t_ms": 2000, "strength": 0.85, "source": "sentence",
    }
    a = bi.build_driver_chunk(driver, 0)
    b = bi.build_driver_chunk(driver, 0)
    assert a["ref_id"] == b["ref_id"] == "hook_open:0"
    assert a["chunk_kind"] == bi.CHUNK_DRIVER
    assert "开场钩子" in a["content"] and "第一句反问" in a["content"]


def test_element_chunk_reuses_payload_style():
    row = {
        "id": "e1", "name": "痛点前置", "category": "开场",
        "usage_steps": "先列三个痛点", "mechanism": "共鸣",
        "counter_example": "不要先自我介绍",
    }
    ch = bi.build_element_chunk("lib_method", row)
    assert ch["ref_table"] == "lib_method"
    assert "痛点前置" in ch["content"] and "共鸣" in ch["content"]


# --------------------------------------------------------------- 2. hash 幂等
def test_content_hash_stable_and_sensitive():
    assert bi.chunk_content_hash("同一段分块文本") == bi.chunk_content_hash("同一段分块文本")
    assert bi.chunk_content_hash("文本A") != bi.chunk_content_hash("文本B")
    assert len(bi.chunk_content_hash("任意文本")) == 32  # md5 hex
    # 空串容错不炸
    assert bi.chunk_content_hash("") == bi.chunk_content_hash(None)


# --------------------------------------------------------------- 3. 融合排序
def test_hybrid_fusion_uses_07_03_weights():
    c1 = _cand("a", "unrelated words only 12345")  # 关键词零命中，纯向量分
    c2 = _cand("b", "焦虑 开场 痛点 留人")
    vec_scores = {"a": 0.90, "b": 0.20}
    ranked, mode = bi.rank_candidates("焦虑开场", [c1, c2], vec_scores, top_k=5)
    assert mode == "hybrid"
    score_a = next(x for x in ranked if x["id"] == "a")["score"]
    assert abs(score_a - (bi.VECTOR_WEIGHT * 0.90 + bi.KEYWORD_WEIGHT * 0.0)) < 1e-9
    score_b = next(x for x in ranked if x["id"] == "b")["score"]
    expected_b = bi.VECTOR_WEIGHT * 0.20 + bi.KEYWORD_WEIGHT * bm25_score("焦虑开场", c2["content"])
    assert abs(score_b - expected_b) < 1e-9
    # 高向量分的 c1 排在关键词命中的 c2 前
    assert ranked[0]["id"] == "a"


def test_fallback_pure_bm25_when_vectors_unavailable():
    c1 = _cand("a", "完全无关的堆砌内容")
    c2 = _cand("b", "钩子开场留人话术三连")
    ranked, mode = bi.rank_candidates("开场留人", [c1, c2], {}, top_k=5)
    assert mode == "keyword_fallback"
    top = ranked[0]
    assert top["id"] == "b"
    assert abs(top["score"] - bm25_score("开场留人", c2["content"])) < 1e-9


def test_rank_empty_candidates():
    ranked, mode = bi.rank_candidates("任意查询", {}, {}, top_k=5)
    assert ranked == []
    assert mode == "keyword_fallback"


# --------------------------------------------------------------- 4. 过滤
def test_filter_by_owner_and_kind():
    c1 = _cand("a", "x", kind="hook", owner="default")
    c2 = _cand("b", "y", kind="segment", owner="alice")
    assert [c["id"] for c in bi.filter_chunks([c1, c2], owner_key="default")] == ["a"]
    assert [c["id"] for c in bi.filter_chunks([c1, c2], kinds=["segment"])] == ["b"]
    assert bi.filter_chunks([c1, c2], owner_key="nobody") == []
    assert bi.filter_chunks([]) == []


# --------------------------------------------------------------- 5. 空 query 短路（不触 DB/网络）
@pytest.mark.asyncio
async def test_search_empty_query_returns_empty_items():
    result = await bi.search_chunks(None, "   ", top_k=5)
    assert result["items"] == []
    assert result["mode"] in ("hybrid", "keyword_fallback")
