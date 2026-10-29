"""创作素材召回服务 + chat RAG 纯函数离线单测。

不连真实库、不打网络、不调 Ollama / LLM：
- creation_materials：归一化、双路融合去重、打分排序、core_entries 转换、
  ref_video_ids 收集、空输入兜底；
- chat 路由抽出的纯函数：拆解参考段落拼装、citations 去重、find_content 两段式拼装。
DB 薄封装（``recall_materials`` 两路取数）依赖真实表结构，不在本文件覆盖。
"""
from __future__ import annotations

import pytest

from app.services import creation_materials as cm
from app.routers import chat as chat_router
from app.routers import creations as creations_router


# ==============================================================
# 构造器：假 chunk / 假 element（形状对齐服务层归一化输入）
# ==============================================================
def _chunk(**over):
    base = {
        "chunk_kind": "hook",
        "title": "钩子：前三秒留人",
        "content": "开头直接抛痛点，制造悬念，配合快节奏剪辑",
        "score": 0.55,
        "video_id": "v2",
        "script_id": "s2",
        "ref_table": "lib_hook",
        "ref_id": "h1",
        "url": "https://example.com/v2",
        "platform": "douyin",
        "video_title": "来源爆款片",
    }
    base.update(over)
    return base


def _element(**over):
    base = {
        "id": "lib_hook:abc123",
        "element_table": "lib_hook",
        "category": "钩子",
        "name": "痛点前置句式",
        "description": "开头三秒抛出用户最痛的问题",
        "formula": "你是不是也……",
        "status": "accepted",
        "tags": ["开场", "痛点"],
        "video": {"id": "v9", "title": "元素来源片", "platform": "bilibili"},
    }
    base.update(over)
    return base


# ==============================================================
# 1. 归一化
# ==============================================================
def test_normalize_chunk_shape():
    item = cm.normalize_chunk(_chunk())
    assert item["source"] == "chunk"
    assert item["kind"] == "hook"
    assert item["ref_table"] == "lib_hook"
    assert item["ref_id"] == "h1"
    assert item["video_id"] == "v2"
    assert item["video_title"] == "来源爆款片"
    assert item["url"] == "https://example.com/v2"
    assert isinstance(item["score"], float)


def test_normalize_element_keeps_composite_id_and_video_meta():
    item = cm.normalize_element(_element(), score=0.42)
    assert item["source"] == "element"
    assert item["element_id"] == "lib_hook:abc123"
    assert item["ref_table"] == "lib_hook"
    assert item["ref_id"] == "abc123"
    assert item["video_id"] == "v9"
    assert item["video_title"] == "元素来源片"
    assert item["score"] == pytest.approx(0.42)


# ==============================================================
# 2. 融合去重 + 排序 + 截断（merge_and_rank 纯函数）
# ==============================================================
def test_merge_dedup_same_ref_keeps_higher_score():
    a = cm.normalize_chunk(_chunk(score=0.30))
    b = cm.normalize_chunk(_chunk(score=0.82))  # 同 ref_table+ref_id
    out = cm.merge_and_rank([a, b], top_k=10)
    assert len(out) == 1
    assert out[0]["score"] == pytest.approx(0.82)


def test_merge_cross_source_collision_keeps_better_score():
    # 同一 lib_hook:h1 行：chunk 路 0.5，element 路 0.6，应只留高分 element
    chunk_item = cm.normalize_chunk(_chunk(score=0.50))
    elem_item = cm.normalize_element(_element(id="lib_hook:h1"), score=0.60)
    out = cm.merge_and_rank([chunk_item, elem_item], top_k=10)
    assert len(out) == 1
    assert out[0]["source"] == "element"


def test_merge_sorted_desc_and_truncated():
    items = [
        cm.normalize_chunk(_chunk(ref_id=f"c{i}", score=float(i) / 10))
        for i in range(5)
    ]
    out = cm.merge_and_rank(items, top_k=3)
    assert len(out) == 3
    scores = [x["score"] for x in out]
    assert scores == sorted(scores, reverse=True)


def test_merge_empty_inputs_safe():
    assert cm.merge_and_rank([]) == []
    assert cm.merge_and_rank(None) == []
    assert cm.merge_and_rank([{"junk": 1}]) == []


# ==============================================================
# 3. core_entries 转换 + ref_video_ids 收集
# ==============================================================
def test_core_entries_chunk_prefixed_and_element_keeps_id():
    materials = [
        cm.normalize_element(_element(), score=0.6),
        cm.normalize_chunk(_chunk(
            chunk_kind="reusable_element", ref_table="lib_method",
            ref_id="m1", script_id="s3", video_id="v3",
        )),
    ]
    entries, vids = cm.materials_to_core_entries(materials)
    assert entries[0]["element_id"] == "lib_hook:abc123"
    assert entries[0]["role"] == "钩子"
    assert entries[0]["order"] == 0
    assert entries[1]["element_id"] == "chunk:lib_method:m1"
    assert entries[1]["role"] == "可复用积木"
    assert entries[1]["order"] == 1
    assert vids == ["v9", "v3"]


def test_collect_ref_video_ids_dedup_keep_order():
    materials = [
        {"video_id": "v1"},
        {"video_id": "v2"},
        {"video_id": "v1"},
        {"video_id": ""},
        {},
    ]
    assert cm.collect_ref_video_ids(materials) == ["v1", "v2"]


def test_core_entries_empty_and_missing_id_skipped():
    entries, vids = cm.materials_to_core_entries([])
    assert entries == [] and vids == []
    # 既无 element_id 也无 ref_id 的条目跳过
    bad = [{"source": "chunk", "ref_table": "", "ref_id": ""}]
    entries, _ = cm.materials_to_core_entries(bad)
    assert entries == []


# ==============================================================
# 4. 召回入口：空意图短路（不触 DB）
# ==============================================================
@pytest.mark.asyncio
async def test_recall_empty_intent_returns_empty_without_db():
    assert await cm.recall_materials(None, "") == []
    assert await cm.recall_materials(None, "   ") == []
    assert await cm.recall_materials(None, None) == []


# ==============================================================
# 5. chat 纯函数：拆解参考段落 / citations 去重 / 两段式回复
# ==============================================================
def test_format_breakdown_refs_nonempty_and_empty():
    text = chat_router.format_breakdown_refs([_chunk()])
    assert "历史拆解参考" in text
    assert "钩子" in text
    assert "来源爆款片" in text
    assert "不代表用户偏好" in text
    assert chat_router.format_breakdown_refs([]) == ""
    assert chat_router.format_breakdown_refs(None) == ""


def test_dedupe_citations_keeps_old_fields_and_drops_dup():
    c1 = {"title": "T1", "platform": "douyin", "url": "u1", "chunk_kind": "hook", "score": 0.5, "video_id": "v1"}
    c2 = dict(c1)  # 同 video_id+url+chunk_kind → 去重
    c3 = {"title": "T2", "platform": "", "url": "", "chunk_kind": "segment", "score": 0.3, "video_id": "v2"}
    out = chat_router.dedupe_citations([c1, c2, c3])
    assert len(out) == 2
    # 旧契约字段不丢
    assert {"title", "platform", "url"} <= set(out[0].keys())


def test_find_content_reply_has_two_sections_and_new_citation_fields():
    whole = [{
        "script_id": "s1", "title": "整片标题", "platform": "douyin",
        "url": "u1", "video_id": "v1", "idea": "核心思想一句话", "score": 0.8,
    }]
    chunks = [
        _chunk(script_id="s2", video_id="v2", title="素材点一", content="钩子话术细节"),
        _chunk(script_id="s1", video_id="v1", title="重复脚本的块", content="应被整片段覆盖掉"),
    ]
    text, citations = chat_router._assemble_find_content_reply("怎么做好开场", whole, chunks, "（顺着关注）")
    assert "【整片拆解】" in text
    assert "【可复用素材点】" in text
    assert "素材点一" in text
    assert "重复脚本的块" not in text  # script_id 与整片重复 → 不挂素材点
    kinds = {c["chunk_kind"] for c in citations}
    assert "script_summary" in kinds
    assert "hook" in kinds
    for c in citations:
        assert "score" in c
        assert "video_id" in c
        assert "title" in c and "platform" in c and "url" in c


def test_find_content_reply_empty_both_sources_skipped_by_caller():
    # 纯拼装函数在两路皆空时仍产出通用收尾；空态文案由 handler 层判定
    text, citations = chat_router._assemble_find_content_reply("找不到的主题", [], [])
    assert citations == []
    assert "要我拆个类似的新视频吗" in text


# ==============================================================
# 6. creations 路由纯辅助：查询词兜底 + 推荐素材卡片
# ==============================================================
class _Msg:
    def __init__(self, role, content):
        self.role = role
        self.content = content


def test_intent_query_prefers_intent_then_last_user_message():
    msgs = [
        _Msg("user", "先问的一句"),
        _Msg("assistant", "答"),
        _Msg("user", "最后这句创作诉求"),
    ]
    assert creations_router._intent_query(msgs, "美妆带货") == "美妆带货"
    assert creations_router._intent_query(msgs, None) == "最后这句创作诉求"
    assert creations_router._intent_query([], None) == ""


def test_recommended_cards_renders_both_sources():
    materials = [
        {"source": "element", "kind": "钩子", "name": "痛点前置", "summary": "开头抛痛点", "score": 0.6},
        {"source": "chunk", "kind": "hook", "name": "开场三秒", "summary": "悬念话术", "score": 0.4},
    ]
    text = creations_router._recommended_cards(materials)
    assert "元素库" in text
    assert "拆解素材" in text
    assert "痛点前置" in text
    assert creations_router._recommended_cards([]) == ""
