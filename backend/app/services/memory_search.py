"""记忆混合检索（向量 + BM25）。

受 OpenClaw / QwenPaw 启发：
- 向量相似度（0.7 权重）：语义匹配，措辞不同也能命中
- BM25 关键词（0.3 权重）：精确 token（ID、代码符号、专有名词）
- 加权融合：final = 0.7 * vector + 0.3 * keyword

实现：user_memory 写入时同步向量化到 lib_embedding（target_table='user_memory'），
召回时双路检索加权融合。
"""
from __future__ import annotations

import logging
import re
from typing import Any

import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.embedding import EMBED_DIM, embed_texts, upsert_one

logger = logging.getLogger(__name__)

VECTOR_WEIGHT = 0.7
KEYWORD_WEIGHT = 0.3


# ==============================================================
# 1. BM25 简化实现（字符级，短文本够用）
# ==============================================================
def _tokenize(text: str) -> list[str]:
    """简单分词：英文/数字按词切，中文按 2-gram 切。"""
    if not text:
        return []
    # 提取英文词、数字、中文
    tokens: list[str] = []
    # 英文和数字
    for m in re.findall(r"[a-zA-Z0-9_]+", text):
        tokens.append(m.lower())
    # 中文：连续中文按 2-gram
    cn = re.findall(r"[\u4e00-\u9fff]+", text)
    for seg in cn:
        if len(seg) == 1:
            tokens.append(seg)
        else:
            for i in range(len(seg) - 1):
                tokens.append(seg[i : i + 2])
    return tokens


def bm25_score(query: str, doc: str) -> float:
    """简化 BM25：命中比例 + 完整短语加分。返回 0~1。"""
    if not query or not doc:
        return 0.0
    q_tokens = _tokenize(query)
    d_tokens = set(_tokenize(doc))
    if not q_tokens:
        return 0.0

    hit = sum(1 for t in q_tokens if t in d_tokens)
    base = hit / len(q_tokens)

    # 完整短语加分：query 原串（去掉空白）出现在 doc 里
    q_plain = re.sub(r"\s+", "", query)
    d_plain = re.sub(r"\s+", "", doc)
    phrase_bonus = 0.2 if len(q_plain) >= 2 and q_plain in d_plain else 0.0

    return min(1.0, base + phrase_bonus)


# ==============================================================
# 2. 记忆写入（user_memory + 向量同步）
# ==============================================================
async def remember(
    db: AsyncSession,
    user_id: str,
    content: str,
    memory_type: str = "preference",
    importance: int = 5,
) -> str:
    """写入一条记忆并同步向量化。返回记忆 id。"""
    import uuid

    mem_id = uuid.uuid4().hex
    await db.execute(text(
        "INSERT INTO user_memory (id, user_id, memory_type, content, importance) "
        "VALUES (:mid, :uid, :mt, :c, :imp)"
    ), {"mid": mem_id, "uid": user_id, "mt": memory_type, "c": content, "imp": importance})

    # 同步向量化（失败不影响记忆写入）
    try:
        vecs = await embed_texts([content])
        await upsert_one(db, "user_memory", mem_id, content, vecs[0])
    except Exception as e:
        logger.warning("记忆向量化失败（不影响写入）：%s", e)

    return mem_id


# ==============================================================
# 3. 混合检索
# ==============================================================
async def hybrid_search(
    db: AsyncSession,
    query: str,
    user_id: str | None = None,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """混合检索用户记忆：向量 0.7 + 关键词 0.3。

    返回 [{id, content, memory_type, importance, score}]，按 score 降序。
    """
    # 3.1 取候选记忆
    sql = "SELECT id, content, memory_type, importance, updated_at FROM user_memory"
    params: dict[str, Any] = {}
    if user_id:
        sql += " WHERE user_id=:uid"
        params["uid"] = user_id
    rows = (await db.execute(text(sql), params)).fetchall()
    if not rows:
        return []

    # 3.2 向量分（从 lib_embedding 查）
    vec_scores: dict[str, float] = {}
    try:
        qvec_list = await embed_texts([query])
        qvec = np.asarray(qvec_list[0], dtype=np.float32)
        qvec = qvec / (np.linalg.norm(qvec) + 1e-10)

        ids = [r[0] for r in rows]
        ph = ",".join(f":id{i}" for i in range(len(ids)))
        emb_rows = (await db.execute(text(
            f"SELECT target_id, embedding, dim FROM lib_embedding "
            f"WHERE target_table='user_memory' AND target_id IN ({ph})"
        ), {f"id{i}": v for i, v in enumerate(ids)})).all()
        # 只取与当前模型维度一致的向量（防 1024→768 迁移期维度混用）
        emb_rows = [r for r in emb_rows if r[2] == EMBED_DIM]
        if emb_rows:
            mat = np.stack([_blob_to_vec(r[1]) for r in emb_rows])
            norms = np.linalg.norm(mat, axis=1, keepdims=True) + 1e-10
            scores = (mat / norms) @ qvec
            for i, r in enumerate(emb_rows):
                vec_scores[r[0]] = float(scores[i])
    except Exception as e:
        logger.warning("记忆向量检索失败（降级为纯关键词）：%s", e)

    # 3.3 融合打分
    results = []
    for r in rows:
        mid, content, mtype, imp, updated_at = r[0], r[1], r[2], r[3], r[4]
        v = vec_scores.get(mid, 0.0)
        k = bm25_score(query, content)
        # 有向量的正常融合；没向量只用关键词
        score = VECTOR_WEIGHT * v + KEYWORD_WEIGHT * k if mid in vec_scores else k
        # 重要性加权（importance 5~10，映射 1.0~1.3）
        score *= 1.0 + min(max(imp, 5), 10) * 0.03 - 0.15
        score *= _recency_factor(updated_at, mtype)
        results.append({
            "id": mid,
            "content": content,
            "memory_type": mtype,
            "importance": imp,
            "score": round(score, 4),
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_k]


def _blob_to_vec(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


def _recency_factor(updated_at, memory_type: str = "preference", now_ts: float | None = None) -> float:
    """记忆时效因子：按记忆类型分级（Mem0 分层生命周期）。

    - preference（核心偏好）：长期记忆，3 天内 1.2x boost，之后缓慢降到 0.8x 封底，
      几乎不遗忘——这是用户稳定的画像
    - dream（结晶洞察）：中期记忆，3 天内 1.5x，60 天线性降到 0.5x 封底
    - feedback（即时反馈）：短期记忆，3 天内 1.5x，14 天快速衰减到 0.3x 封底，
      反馈过期就该淡出，防止陈旧反馈主导召回
    旧记忆不硬删，降权仍可召回（Mem0 Memory Decay 精神）。
    """
    import time
    if updated_at is None:
        return 0.7
    try:
        from datetime import datetime
        if isinstance(updated_at, (int, float)):
            ts = float(updated_at)
        else:
            ts = datetime.strptime(str(updated_at), "%Y-%m-%d %H:%M:%S").timestamp()
    except Exception:
        return 0.7
    ref = now_ts if now_ts is not None else time.time()
    age_days = max(0.0, (ref - ts) / 86400)
    mtype = memory_type or "preference"
    if mtype == "feedback":
        # 短期：14 天衰减到 0.3 封底
        if age_days < 3:
            return 1.5
        if age_days < 14:
            return 1.5 - (age_days - 3) / 11 * 1.2
        return 0.3
    if mtype == "dream":
        # 中期：60 天衰减到 0.5 封底
        if age_days < 3:
            return 1.5
        if age_days < 60:
            return 1.5 - (age_days - 3) / 57 * 1.0
        return 0.5
    # preference：长期，几乎不遗忘
    if age_days < 3:
        return 1.2
    if age_days < 180:
        return 1.2 - (age_days - 3) / 177 * 0.4
    return 0.8  # 半年后封底 0.8，核心偏好永远有效


# ==============================================================
# 4. 冲突检测（Mem0 conflict detection）：语义相似检索
# ==============================================================
async def find_similar_memory(
    db: AsyncSession,
    user_id: str,
    content: str,
    min_sim: float = 0.50,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """找与该用户已有记忆语义最相似的若干条（向量余弦）。

    用于：记住新内容前查重（合并），纠正旧记忆前定位目标。
    返回 [{id, content, memory_type, importance, sim}]，sim 为余弦相似度。
    """
    if not content:
        return []
    try:
        qvec_list = await embed_texts([content])
        qvec = np.asarray(qvec_list[0], dtype=np.float32)
        qvec = qvec / (np.linalg.norm(qvec) + 1e-10)
    except Exception as e:
        logger.warning("冲突检测向量化失败（跳过）：%s", e)
        return []

    # 该用户的全部记忆 id
    rows = (await db.execute(text(
        "SELECT id FROM user_memory WHERE user_id=:uid"
    ), {"uid": user_id})).fetchall()
    ids = [r[0] for r in rows]
    if not ids:
        return []

    ph = ",".join(f":id{i}" for i in range(len(ids)))
    emb_rows = (await db.execute(text(
        f"SELECT target_id, embedding, dim FROM lib_embedding "
        f"WHERE target_table='user_memory' AND target_id IN ({ph})"
    ), {f"id{i}": v for i, v in enumerate(ids)})).fetchall()
    emb_rows = [r for r in emb_rows if r[2] == EMBED_DIM]
    if not emb_rows:
        return []

    mat = np.stack([_blob_to_vec(r[1]) for r in emb_rows])
    norms = np.linalg.norm(mat, axis=1, keepdims=True) + 1e-10
    scores = (mat / norms) @ qvec

    cand: list[tuple[str, float]] = []
    for i, r in enumerate(emb_rows):
        s = float(scores[i])
        if s >= min_sim:
            cand.append((r[0], s))
    cand.sort(key=lambda x: x[1], reverse=True)
    cand = cand[:limit]
    if not cand:
        return []

    out: list[dict[str, Any]] = []
    for mid, sim in cand:
        info = (await db.execute(text(
            "SELECT content, memory_type, importance FROM user_memory WHERE id=:mid"
        ), {"mid": mid})).fetchone()
        if info:
            out.append({
                "id": mid,
                "content": info[0],
                "memory_type": info[1],
                "importance": info[2],
                "sim": round(sim, 4),
            })
    return out
