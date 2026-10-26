"""素材库语义向量化服务（第一阶段：暴力余弦，不上向量库）。

为什么不上 Qdrant / Milvus / Chroma？
- 当前素材总量 ~180 条（hook/quote/copywriting/topic/method），向量矩阵
  180 × 1024 × 4B ≈ 0.7 MB，numpy 全量余弦 < 1ms。
- 当素材爬到 10w+ 条时再引入 ANN（sqlite-vec / hnswlib），现在上重型
  向量库纯属过度工程。

链路：
  文本 → 智谱 embedding-3 → 1024维 float32 向量 → SQLite BLOB
  query 文本 → 同模型向量化 → numpy 余弦 → top-K
"""
from __future__ import annotations

import hashlib
import logging
import time
import uuid
from typing import Any

import httpx
import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import GLM_API_KEY

logger = logging.getLogger(__name__)

# --- 智谱 embedding-3（OpenAI 兼容协议）---
EMBED_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
EMBED_MODEL = "embedding-3"
EMBED_DIM = 1024  # 支持 2048/1024/512/256；1024 在召回质量和存储间最优
EMBED_BATCH = 32  # 智谱单次请求条数上限保守取 32


# ==============================================================
# 1. 远程 embedding（带内存缓存：同内容不重复调 API）
# ==============================================================
# 内存缓存：content_hash -> vector。上限条数，防止无限增长。
_EMBED_CACHE: dict[str, list[float]] = {}
_EMBED_CACHE_MAX = 5000
_EMBED_CACHE_HITS = 0


def _cache_get(text: str) -> list[float] | None:
    h = _hash_text(text)
    return _EMBED_CACHE.get(h)


def _cache_set(text: str, vector: list[float]) -> None:
    global _EMBED_CACHE
    h = _hash_text(text)
    if len(_EMBED_CACHE) >= _EMBED_CACHE_MAX:
        # 满了：清一半最旧的（dict 保持插入序，弹掉前 1/4）
        drop = _EMBED_CACHE_MAX // 4
        for k in list(_EMBED_CACHE)[:drop]:
            del _EMBED_CACHE[k]
    _EMBED_CACHE[h] = vector


def embed_cache_stats() -> dict:
    """缓存统计，方便监控。"""
    return {
        "cache_size": len(_EMBED_CACHE),
        "cache_hits": _EMBED_CACHE_HITS,
        "cache_max": _EMBED_CACHE_MAX,
    }


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """批量把文本转成向量。自动按 32 条一批分片。

    带内存缓存：命中缓存的文本直接复用向量，不调 API（省 token 和限流）。
    失败抛异常，由调用方决定重试/降级。
    """
    global _EMBED_CACHE_HITS
    if not texts:
        return []
    if not GLM_API_KEY:
        raise RuntimeError("GLM_API_KEY 未配置，无法调用 embedding")

    # 1. 查缓存：逐条命中
    results: list[list[float]] = []
    todo: list[tuple[int, str]] = []  # (index, text) 需要调 API 的
    for i, t in enumerate(texts):
        cached = _cache_get(t)
        if cached is not None:
            results.append(cached)
            _EMBED_CACHE_HITS += 1
        else:
            results.append(None)  # 占位
            todo.append((i, t))

    if not todo:
        return results  # 全命中

    # 2. 调 API（只算没命中的）
    headers = {
        "Authorization": f"Bearer {GLM_API_KEY}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        for j in range(0, len(todo), EMBED_BATCH):
            batch = todo[j : j + EMBED_BATCH]
            chunk_texts = [t for _, t in batch]
            payload = {
                "model": EMBED_MODEL,
                "input": chunk_texts,
                "dimensions": EMBED_DIM,
                "encoding_format": "float",
            }
            resp = await client.post(
                f"{EMBED_BASE_URL}/embeddings", headers=headers, json=payload
            )
            resp.raise_for_status()
            data = resp.json()
            items = sorted(data["data"], key=lambda x: x["index"])
            for idx, item in zip([i for i, _ in batch], items):
                vec = item["embedding"]
                results[idx] = vec
                _cache_set(texts[idx], vec)
            usage = data.get("usage", {})
            logger.info(
                "embedding 批次 %d-%d 完成（含缓存命中），tokens=%s",
                j, j + len(batch), usage,
            )
            # 温和限速，避免触发 QPS
            await __sleep(0.2)
    return results


async def __sleep(s: float):
    import asyncio
    await asyncio.sleep(s)


# ==============================================================
# 2. 文本拼装：把一行素材拼成一段自然语言喂给 embedding
# ==============================================================
def build_payload(row: dict[str, Any], table: str) -> str:
    """把 lib_* 一行拼成一段给 embedding 看的文本。

    embedding 不懂"字段"，只懂"语义"。把类型/名称/句式/机制/效果揉成
    一段人话，召回才准。
    """
    r = row
    if table == "lib_hook":
        return (
            f"钩子（{r.get('hook_type','')}，位置{r.get('position','')}）：{r.get('name','')}。"
            f"句式：{r.get('sentence_pattern','')}。"
            f"预期效果：{r.get('expected_effect','')}。"
            f"心理机制：{r.get('mechanism','')}"
        )
    if table == "lib_quote":
        return (
            f"金句：{r.get('text','')}。"
            f"句式结构：{r.get('structure','')}。"
            f"适用场景：{r.get('applicable_scene','')}。"
            f"心理机制：{r.get('mechanism','')}。"
            f"改写模板：{r.get('rewrite_template','')}"
        )
    if table == "lib_copywriting":
        return (
            f"文案（{r.get('copy_type','')}，修辞{r.get('rhetoric','')}）：{r.get('name','')}。"
            f"句式：{r.get('sentence_pattern','')}。"
            f"示例：{r.get('example_text','')}。"
            f"心理机制：{r.get('mechanism','')}"
        )
    if table == "lib_topic":
        return (
            f"选题（{r.get('topic_type','')}）：{r.get('name','')}。"
            f"受众：{r.get('audience','')}。"
            f"痛点：{r.get('pain_point','')}。"
            f"切入角度：{r.get('angle','')}。"
            f"价值类型：{r.get('value_type','')}"
        )
    if table == "lib_method":
        return (
            f"手法（{r.get('category','')}，{r.get('abstraction_level','')}）：{r.get('name','')}。"
            f"操作步骤：{r.get('usage_steps','')}。"
            f"心理机制：{r.get('mechanism','')}。"
            f"反例：{r.get('counter_example','')}"
        )
    # 兜底：拼所有非空字段
    return " ".join(f"{k}={v}" for k, v in r.items() if v and isinstance(v, str))


# ==============================================================
# 3. 向量读写
# ==============================================================
def _hash_text(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _vec_to_blob(vec: list[float]) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


def _blob_to_vec(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


async def upsert_one(
    db: AsyncSession,
    table: str,
    target_id: str,
    content: str,
    vector: list[float],
) -> bool:
    """写入或更新一条向量。返回是否发生了写入。"""
    now = time.time()
    h = _hash_text(content)
    await db.execute(
        text("""
            INSERT INTO lib_embedding
              (id, target_table, target_id, content_hash, embedding, dim, model, created_at, updated_at)
            VALUES
              (:id, :tbl, :tid, :h, :emb, :dim, :model, :now, :now)
            ON CONFLICT(target_table, target_id) DO UPDATE SET
              content_hash=excluded.content_hash,
              embedding=excluded.embedding,
              dim=excluded.dim,
              model=excluded.model,
              updated_at=excluded.updated_at
        """),
        {
            "id": uuid.uuid4().hex,
            "tbl": table,
            "tid": str(target_id),
            "h": h,
            "emb": _vec_to_blob(vector),
            "dim": len(vector),
            "model": EMBED_MODEL,
            "now": now,
        },
    )
    await db.commit()
    return True


async def get_need_embed_ids(
    db: AsyncSession, table: str, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """对一批行，过滤出"还没向量化 / 文本变了需要重算"的。"""
    if not rows:
        return []
    ids = [str(r["id"]) for r in rows]
    placeholders = ",".join(f":id{i}" for i in range(len(ids)))
    existing = await db.execute(
        text(
            f"SELECT target_id, content_hash FROM lib_embedding "
            f"WHERE target_table=:tbl AND target_id IN ({placeholders})"
        ),
        {"tbl": table, **{f"id{i}": tid for i, tid in enumerate(ids)}},
    )
    have = {row[0]: row[1] for row in existing.all()}
    todo = []
    for r in rows:
        tid = str(r["id"])
        text_to_embed = build_payload(r, table)
        h = _hash_text(text_to_embed)
        if have.get(tid) != h:
            todo.append({"row": r, "tid": tid, "text": text_to_embed})
    return todo


# ==============================================================
# 4. 语义检索（numpy 暴力余弦）
# ==============================================================
async def search(
    db: AsyncSession,
    query: str,
    target_table: str | None = None,
    top_k: int = 10,
) -> list[dict[str, Any]]:
    """语义检索：query → embed → 全表 cosine → top-K。

    返回 [{target_table, target_id, score}]。
    """
    # 4.1 query 向量化
    qvec_list = await embed_texts([query])
    qvec = np.asarray(qvec_list[0], dtype=np.float32)
    qvec = qvec / (np.linalg.norm(qvec) + 1e-10)

    # 4.2 捞所有向量
    sql = "SELECT target_table, target_id, embedding FROM lib_embedding"
    params: dict[str, Any] = {}
    if target_table:
        sql += " WHERE target_table=:tbl"
        params["tbl"] = target_table
    rows = (await db.execute(text(sql), params)).all()
    if not rows:
        return []

    # 4.3 拼成矩阵算余弦
    mat = np.stack([_blob_to_vec(r[2]) for r in rows])
    norms = np.linalg.norm(mat, axis=1, keepdims=True) + 1e-10
    mat_norm = mat / norms
    scores = mat_norm @ qvec

    # 4.4 top-K
    k = min(top_k, len(rows))
    top_idx = np.argpartition(-scores, k - 1)[:k]
    order = top_idx[np.argsort(-scores[top_idx])]

    return [
        {
            "target_table": rows[i][0],
            "target_id": rows[i][1],
            "score": float(scores[i]),
        }
        for i in order
    ]
