"""创作素材召回服务（P1 创作台消费层）。

双路本地召回，纯检索不调 LLM、不出本机：
  路 1 · 拆解分块 ``breakdown_chunk``：``bidx.search_chunks`` 混合检索
        （向量 0.7 + BM25 0.3），取钩子 / 可复用积木 / 完播驱动三类创作可用点；
  路 2 · 元素库 ``lib_*``：复用 ``element_library.list_elements`` 读取（不另造
        取数 SQL），排除 rejected 后用 ``memory_search.bm25_score`` 对意图打分排序。

融合规则（纯函数 ``merge_and_rank``，离线可测）：
  - 去重键 ``(ref_table, ref_id)``：同一条底层记录同时被两路命中时只留高分；
  - 两路分数分别乘权重后按降序截断 top_k。

落库约定（不新建表，关联走 ``creations`` 现有 JSON 列）：
  - ``core_elements``：``materials_to_core_entries`` 产出
    ``[{element_id, role, order}]``；chunk 引用用稳定前缀 id ``chunk:{ref_table}:{ref_id}``，
    element 沿用 ``{table}:{uuid}`` 复合 id；
  - ``ref_video_ids``：``collect_ref_video_ids`` 按顺序去重收集来源 video_id。
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import breakdown_index as bidx
from app.services import element_library
from app.services.memory_search import bm25_score

logger = logging.getLogger(__name__)

# ==============================================================
# 常量（魔法数收口，便于调参与单测引用）
# ==============================================================
DEFAULT_TOP_K = 6                 # 默认召回条数
ELEMENT_POOL_LIMIT = 120          # 元素库候选池上限（打分前先捞这么多，再按意图排序）
CHUNK_PREVIEW_LIMIT = 160         # summary 截断长度
SCORE_WEIGHT_CHUNK = 1.0          # 分块路分数权重（分块分已是 0.7 向量 + 0.3 BM25 融合值）
SCORE_WEIGHT_ELEMENT = 1.0         # 元素路分数权重（BM25 0~1，量级与分块分接近，默认等权）

# 创作召回纳入的分块类型（钩子 / 可复用积木 / 完播驱动；整片概览与段落不进素材池）
CHUNK_KINDS: tuple[str, ...] = (
    bidx.CHUNK_HOOK,
    bidx.CHUNK_ELEMENT,
    bidx.CHUNK_DRIVER,
)

CHUNK_ID_PREFIX = "chunk:"        # chunk 引用在 core_elements 中的稳定 id 前缀
REJECTED_STATUS = "rejected"      # 元素库质控拒绝态，不进召回池

# kind → core_elements.role 中文标签（chunk kind 与元素表名共用一张映射表）
ROLE_BY_KIND: dict[str, str] = {
    bidx.CHUNK_HOOK: "钩子",
    bidx.CHUNK_ELEMENT: "可复用积木",
    bidx.CHUNK_DRIVER: "完播驱动",
    "lib_hook": "钩子",
    "lib_topic": "选题",
    "lib_copywriting": "话术",
    "lib_quote": "话术",
    "lib_method": "剪辑手法",
    "lib_combo": "结构",
}


# ==============================================================
# 1. 纯函数：归一化 / 融合去重 / 排序（无 DB、无网络，离线可测）
# ==============================================================
def normalize_chunk(chunk: dict[str, Any]) -> dict[str, Any]:
    """``bidx.search_chunks`` 命中项 → 统一素材形状。"""
    return {
        "source": "chunk",
        "kind": chunk.get("chunk_kind") or "",
        "name": chunk.get("title") or chunk.get("chunk_kind") or "",
        "summary": (chunk.get("content") or "")[:CHUNK_PREVIEW_LIMIT],
        "score": round(float(chunk.get("score") or 0.0) * SCORE_WEIGHT_CHUNK, 4),
        "ref_table": chunk.get("ref_table"),
        "ref_id": chunk.get("ref_id"),
        "video_id": chunk.get("video_id"),
        "video_title": chunk.get("video_title"),
        "url": chunk.get("url"),
        "platform": chunk.get("platform"),
    }


def _element_haystack(element: dict[str, Any]) -> str:
    """元素打分文本：名称 + 描述 + 配方 + 标签，拼起来供 BM25 命中。"""
    parts: list[str] = [
        str(element.get("name") or ""),
        str(element.get("description") or ""),
        str(element.get("formula") or ""),
    ]
    parts.extend(str(t) for t in (element.get("tags") or []))
    return " ".join(p for p in parts if p)


def normalize_element(element: dict[str, Any], score: float) -> dict[str, Any]:
    """``element_library.serialize`` 产物 → 统一素材形状（保留复合 id 供落库）。"""
    video = element.get("video") or {}
    raw_id = str(element.get("id") or "")
    ref_table = element.get("element_table") or ""
    # 复合 id "lib_hook:uuid" → ref_id 取 uuid 段；无冒号则整串兜底
    ref_id = raw_id.split(":", 1)[1] if ":" in raw_id else raw_id
    return {
        "source": "element",
        "kind": element.get("element_table") or element.get("category") or "",
        "name": element.get("name") or "",
        "summary": (element.get("description") or element.get("formula") or "")[:CHUNK_PREVIEW_LIMIT],
        "score": round(float(score) * SCORE_WEIGHT_ELEMENT, 4),
        "ref_table": ref_table,
        "ref_id": ref_id,
        "video_id": video.get("id"),
        "video_title": video.get("title"),
        "url": None,
        "platform": video.get("platform"),
        # 内部附加字段：落 core_elements 时 element 路沿用复合 id，不进对外契约展示
        "element_id": raw_id,
    }


def _dedup_key(item: dict[str, Any]) -> tuple[str, str, str]:
    """融合去重键：优先 (ref_table, ref_id)；两者缺失时退化为 (source, name) 防误并。"""
    ref_table = str(item.get("ref_table") or "")
    ref_id = str(item.get("ref_id") or "")
    if ref_table and ref_id:
        return ("ref", ref_table, ref_id)
    return ("name", item.get("source") or "", str(item.get("name") or ""))


def merge_and_rank(items: list[dict[str, Any]], top_k: int = DEFAULT_TOP_K) -> list[dict[str, Any]]:
    """合并两路候选：同键只留高分，按 score 降序截断。空输入返回空列表不报错。"""
    best: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        # 既无 (ref_table, ref_id) 又无名称的脏条目不参与融合
        if not (item.get("ref_table") and item.get("ref_id")) and not item.get("name"):
            continue
        key = _dedup_key(item)
        old = best.get(key)
        if old is None or float(item.get("score") or 0.0) > float(old.get("score") or 0.0):
            best[key] = item
    ranked = sorted(best.values(), key=lambda x: float(x.get("score") or 0.0), reverse=True)
    return ranked[: max(0, top_k)]


def core_entry_id(material: dict[str, Any]) -> str:
    """素材 → ``core_elements.element_id`` 稳定 id；无法生成时返回空串（由上层跳过）。

    - element 路：沿用 ``{table}:{uuid}`` 复合 id（创作链路既有约定）；
    - chunk 路：``chunk:{ref_table}:{ref_id}`` 前缀命名空间，避免与元素 id 撞车；
    - 两路都拿不到 ref 标识时返回 ""，不写 ``chunk::`` 这类无效占位 id。
    """
    if material.get("source") == "element":
        return str(material.get("element_id") or material.get("ref_id") or "")
    ref_table = str(material.get("ref_table") or "")
    ref_id = str(material.get("ref_id") or "")
    if not ref_id:
        return ""
    return f"{CHUNK_ID_PREFIX}{ref_table}:{ref_id}"


def collect_ref_video_ids(materials: list[dict[str, Any]]) -> list[str]:
    """按出现顺序去重收集来源 video_id（供 ``Creation.ref_video_ids`` JSON 列）。"""
    out: list[str] = []
    seen: set[str] = set()
    for m in materials or []:
        vid = m.get("video_id")
        if not vid:
            continue
        vid = str(vid)
        if vid not in seen:
            seen.add(vid)
            out.append(vid)
    return out


def materials_to_core_entries(
    materials: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """召回结果 → ``Creation.core_elements`` 形状 + 来源视频去重列表。

    返回 ``(entries, ref_video_ids)``：
    entries = ``[{element_id, role, order}]``，order 从 0 起按召回顺序编号；
    无法生成稳定 id 的条目跳过；空输入返回 ``([], [])``。
    """
    entries: list[dict[str, Any]] = []
    for i, m in enumerate(materials or []):
        if not isinstance(m, dict):
            continue
        eid = core_entry_id(m)
        if not eid:
            continue
        role = ROLE_BY_KIND.get(m.get("kind") or "", m.get("kind") or "素材")
        entries.append({"element_id": eid, "role": role, "order": i})
    return entries, collect_ref_video_ids(materials)


# ==============================================================
# 2. DB 薄封装：两路召回（异常一律 try/warning 静默降级为空）
# ==============================================================
async def _recall_chunks(db: AsyncSession, query: str, top_k: int) -> list[dict[str, Any]]:
    """路 1：拆解分块混合检索（钩子 / 积木 / 完播驱动）。"""
    try:
        result = await bidx.search_chunks(db, query, kinds=list(CHUNK_KINDS), top_k=top_k)
        return [normalize_chunk(c) for c in result.get("items", [])]
    except Exception as e:  # noqa: BLE001
        logger.warning("创作素材召回·分块路失败（静默降级为空）：%s", e)
        return []


async def _recall_elements(db: AsyncSession, query: str, category: str | None, top_k: int) -> list[dict[str, Any]]:
    """路 2：元素库 active（非 rejected）候选池 + BM25 意图打分排序。"""
    try:
        pool = await element_library.list_elements(
            db,
            category=category or None,
            limit=ELEMENT_POOL_LIMIT,
        )
        scored: list[dict[str, Any]] = []
        for e in pool.get("items", []):
            if e.get("status") == REJECTED_STATUS:
                continue
            score = bm25_score(query, _element_haystack(e))
            if score <= 0:
                continue
            scored.append(normalize_element(e, score))
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]
    except Exception as e:  # noqa: BLE001
        logger.warning("创作素材召回·元素库路失败（静默降级为空）：%s", e)
        return []


async def recall_materials(
    db: AsyncSession,
    intent: str,
    category: str | None = None,
    top_k: int = DEFAULT_TOP_K,
) -> list[dict[str, Any]]:
    """按创作意图双路召回素材，融合去重后按相关度降序返回。

    - 空 intent / 空查询直接返回空列表，不查库、不报错；
    - 任一路异常静默降级，绝不向上抛错影响创作主流程；
    - 统一输出 ``{source, kind, name, summary, score, ref_table, ref_id,
      video_id, video_title, url}``（element 路内部另带 ``element_id`` 供落库）。
    """
    query = (intent or "").strip()
    if not query:
        return []
    chunk_items = await _recall_chunks(db, query, top_k)
    element_items = await _recall_elements(db, query, category, top_k)
    return merge_and_rank(chunk_items + element_items, top_k=top_k)
