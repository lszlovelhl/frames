"""拆解结果分块混合索引（P1 地基：RAG 问答与创作储备的检索层）。

链路：
  拆解产物（script_* / lib_* / evidence）→ 分块（自然语言中文段落）
  → 本地 Ollama nomic-embed-text（768 维 float32）→ SQLite BLOB 落 ``breakdown_chunk``
  → 检索时 向量余弦 0.7 + 简版 BM25 0.3 加权融合；本地 embedding 不可用
    自动降级纯 BM25/关键词，绝不静默把文本发往云端。

为什么不上 sqlite-vec / Chroma / FAISS：
- 规模估算：单条拆解 ≈ 概览 1 + 段落 5~8 + 钩子 2~5 + 高价值句 5~15
  + 完播驱动 ≤10 + 可复用积木 5~20，约 30~60 块；1000 部视频约 3~6 万块，
  向量矩阵 6w × 768 × 4B ≈ 180MB，numpy 全量余弦在十万级以内仍是毫秒~百毫秒级；
- 与 ``embedding.py`` 既有 numpy 暴力余弦路线保持一致，不新增向量依赖；
- 规模上限经验值：单表块数 > 20 万 或单次检索 > 300ms 时，再评估 ANN
  （sqlite-vec / hnswlib），当前阶段上重型向量库属过度工程。

隔离取舍（弱隔离，docstring 留痕）：
- ``videos`` / ``script_*`` 表没有 user_id 列（历史事实，禁止加列）；
  本表新增 ``owner_key TEXT``，默认 ``'default'``；未来若能从会话上下文取到
  用户标识再按人写入，老数据回填 ``'default'``。当前单机单人使用，弱隔离可接受。

测试约定：分块组装、融合排序、hash 幂等均为不依赖 DB 的纯函数；
embed 调用点在测试中 monkeypatch 替身，不打真实 Ollama。
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.embedding import (
    EMBED_DIM,
    EMBED_MODEL,
    _blob_to_vec,
    _hash_text,
    _vec_to_blob,
    build_payload,
    embed_texts,
)
from app.services.memory_search import bm25_score

logger = logging.getLogger(__name__)

# ==============================================================
# 常量（魔法数收口，便于调参与单测引用）
# ==============================================================
VECTOR_WEIGHT = 0.7          # 向量余弦权重（语义召回）
KEYWORD_WEIGHT = 0.3         # BM25 关键词权重（精确 token / 专有名词）
DEFAULT_TOP_K = 5            # 检索默认条数
CONTENT_PREVIEW_LIMIT = 240  # API 返回 content 截断长度（完整文本仍在库内可用于 RAG 注入）
DEFAULT_OWNER_KEY = "default"

# 分块类型登记册（kind 名稳定，前端/chat citations 按此消费）
CHUNK_SUMMARY = "script_summary"
CHUNK_SEGMENT = "segment"
CHUNK_HOOK = "hook"
CHUNK_SENTENCE = "sentence_function"
CHUNK_DRIVER = "completion_driver"
CHUNK_ELEMENT = "reusable_element"
CHUNK_KINDS = (
    CHUNK_SUMMARY,
    CHUNK_SEGMENT,
    CHUNK_HOOK,
    CHUNK_SENTENCE,
    CHUNK_DRIVER,
    CHUNK_ELEMENT,
)

# 纳入「可复用积木」分块的第三层表（ref_element_source 可关联到本视频）
REUSABLE_TABLES = ("lib_method", "lib_topic", "lib_quote", "lib_copywriting")

# 高价值句子入选条件：强标记位 或 关键功能句
_VALUABLE_SENTENCE_FUNCTIONS = {"钩子", "转折", "高潮", "干货", "CTA"}


# ==============================================================
# 1. 建表（幂等，启动失败不阻断服务）
# ==============================================================
async def ensure_breakdown_index_schema(db: AsyncSession) -> None:
    """幂等建表 ``breakdown_chunk`` + 索引。

    唯一键 ``(owner_key, chunk_kind, ref_table, ref_id, seq)`` 保证同一归属下
    同一块不重复落库；重跑 ``index_breakdown`` 先按 script_id 清旧块再重建。
    """
    await db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS breakdown_chunk (
                id           TEXT PRIMARY KEY,
                owner_key    TEXT NOT NULL DEFAULT 'default',
                video_id     TEXT,
                script_id    TEXT,
                chunk_kind   TEXT NOT NULL,
                ref_table    TEXT,
                ref_id       TEXT,
                seq          INTEGER NOT NULL DEFAULT 0,
                title        TEXT DEFAULT '',
                content      TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                embedding    BLOB,
                dim          INTEGER,
                model        TEXT,
                created_at   REAL
            )
            """
        )
    )
    await db.execute(text("CREATE INDEX IF NOT EXISTS ix_bchunk_owner ON breakdown_chunk(owner_key)"))
    await db.execute(text("CREATE INDEX IF NOT EXISTS ix_bchunk_video ON breakdown_chunk(video_id)"))
    await db.execute(text("CREATE INDEX IF NOT EXISTS ix_bchunk_script ON breakdown_chunk(script_id)"))
    await db.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_bchunk_key "
            "ON breakdown_chunk(owner_key, chunk_kind, ref_table, ref_id, seq)"
        )
    )
    await db.commit()


# ==============================================================
# 2. 纯函数：分块组装（无 DB、无网络，离线可测）
# ==============================================================
def chunk_content_hash(content: str) -> str:
    """分块文本幂等指纹：文本未变即跳过重算 embedding。"""
    return _hash_text(content or "")


def _fmt_ms(ms: Any) -> str:
    """毫秒 → mm:ss 展示（容错：非法值返回占位）。"""
    try:
        total = int(ms or 0) // 1000
        return f"{total // 60:02d}:{total % 60:02d}"
    except (TypeError, ValueError):
        return "--:--"


def _short(text: Any, limit: int = 48) -> str:
    s = str(text or "").strip()
    return s if len(s) <= limit else s[:limit] + "…"


def build_summary_chunk(script: dict[str, Any], video_title: str = "") -> dict[str, Any]:
    """script_summary：全片概览（核心思想 + 走向 + 受众 + 摘要 + 质量分）。"""
    quality = script.get("quality_score")
    quality_line = f"\n质量评分：{quality}" if quality not in (None, "") else ""
    content = (
        f"【视频概览】{video_title or ''}\n"
        f"核心思想：{script.get('core_idea') or ''}\n"
        f"内容走向：{script.get('content_trend') or ''}\n"
        f"目标受众：{script.get('target_audience') or ''}\n"
        f"拆解摘要：{script.get('summary') or ''}"
        f"{quality_line}"
    )
    return {
        "chunk_kind": CHUNK_SUMMARY,
        "ref_table": "script_script",
        "ref_id": str(script.get("id") or ""),
        "seq": 0,
        "title": f"概览：{_short(script.get('core_idea'), 24)}",
        "content": content.strip(),
    }


def build_segment_chunk(seg: dict[str, Any], duration_ms: int = 0) -> dict[str, Any]:
    """segment：时间轴段落（类型/目的/摘要 + 起止时间与时长占比）。"""
    start_ms = int(seg.get("start_ms") or 0)
    end_ms = int(seg.get("end_ms") or 0)
    span = f"{_fmt_ms(start_ms)}–{_fmt_ms(end_ms)}"
    total = max(1, int(duration_ms or 0))
    ratio = max(0.0, min(1.0, (end_ms - start_ms) / total))
    content = (
        f"【脚本段落·{seg.get('seg_type') or '段落'}】{seg.get('title') or ''}\n"
        f"位置：{span}（约占全片 {ratio:.0%}）\n"
        f"段落目的：{seg.get('purpose') or ''}\n"
        f"段落摘要：{seg.get('summary') or ''}"
    )
    return {
        "chunk_kind": CHUNK_SEGMENT,
        "ref_table": "script_segment",
        "ref_id": str(seg.get("id") or ""),
        "seq": int(seg.get("seq") or 0),
        "title": f"第{int(seg.get('seq') or 0)}段·{seg.get('seg_type') or '段落'}",
        "content": content.strip(),
    }


def build_segment_hook_chunk(seg: dict[str, Any]) -> dict[str, Any]:
    """hook（段级）：被 hook_point 标记的段落，强调它承担的开场/留人职责。"""
    content = (
        f"【片中标记为钩子的段落】{seg.get('title') or seg.get('seg_type') or ''}\n"
        f"段落类型：{seg.get('seg_type') or ''}，时间 {_fmt_ms(seg.get('start_ms'))}–{_fmt_ms(seg.get('end_ms'))}\n"
        f"留人目的：{seg.get('purpose') or ''}\n"
        f"段落摘要：{seg.get('summary') or ''}"
    )
    return {
        "chunk_kind": CHUNK_HOOK,
        "ref_table": "script_segment",
        "ref_id": str(seg.get("id") or ""),
        "seq": int(seg.get("seq") or 0),
        "title": f"钩子段：{_short(seg.get('title') or seg.get('seg_type'), 24)}",
        "content": content.strip(),
    }


def build_hook_library_chunk(hook: dict[str, Any]) -> dict[str, Any]:
    """hook（库级）：lib_hook 积木（句式 / 预期效果 / 心理机制）。"""
    content = f"【钩子素材·库】{build_payload(hook, 'lib_hook')}"
    return {
        "chunk_kind": CHUNK_HOOK,
        "ref_table": "lib_hook",
        "ref_id": str(hook.get("id") or ""),
        "seq": 0,
        "title": f"钩子：{_short(hook.get('name'), 24)}",
        "content": content.strip(),
    }


def build_sentence_chunk(sent: dict[str, Any]) -> dict[str, Any]:
    """sentence_function：高价值句（钩子/转折/高潮/干货/CTA 或强标记位）。"""
    func = sent.get("sentence_function") or "句子"
    content = (
        f"【高价值句子·{func}】原话：{sent.get('quote') or ''}\n"
        f"作用说明：{sent.get('function_reason') or ''}\n"
        f"时间锚点：{_fmt_ms(sent.get('start_ms'))}–{_fmt_ms(sent.get('end_ms'))}，"
        f"情绪强度 {sent.get('emotion_intensity') if sent.get('emotion_intensity') is not None else '—'}"
    )
    return {
        "chunk_kind": CHUNK_SENTENCE,
        "ref_table": "script_sentence",
        "ref_id": str(sent.get("id") or ""),
        "seq": int(sent.get("seq") or 0),
        "title": f"{func}句：{_short(sent.get('quote'), 20)}",
        "content": content.strip(),
    }


def build_driver_chunk(driver: dict[str, Any], idx: int) -> dict[str, Any]:
    """completion_driver：evidence.completion_drivers 中的一条完播驱动要素。"""
    label = driver.get("label") or driver.get("driver") or "完播驱动"
    content = (
        f"【完播驱动·{label}】类型码：{driver.get('driver') or ''}\n"
        f"证据原话：{driver.get('evidence_quote') or ''}\n"
        f"出现时间：{_fmt_ms(driver.get('t_ms'))}，"
        f"强度 {driver.get('strength') if driver.get('strength') is not None else '—'}，"
        f"信号来源 {driver.get('source') or ''}"
    )
    return {
        "chunk_kind": CHUNK_DRIVER,
        "ref_table": "completion_driver",
        "ref_id": f"{driver.get('driver') or 'driver'}:{idx}",
        "seq": idx,
        "title": f"完播驱动：{_short(label, 24)}",
        "content": content.strip(),
    }


def build_element_chunk(table: str, row: dict[str, Any]) -> dict[str, Any]:
    """reusable_element：lib_method / lib_topic / lib_quote / lib_copywriting 积木。"""
    content = f"【可复用积木·{table}】{build_payload(row, table)}"
    title = row.get("name") or row.get("text") or table
    return {
        "chunk_kind": CHUNK_ELEMENT,
        "ref_table": table,
        "ref_id": str(row.get("id") or ""),
        "seq": 0,
        "title": f"积木：{_short(title, 24)}",
        "content": content.strip(),
    }


# ==============================================================
# 3. 纯函数：过滤与融合排序（离线可测）
# ==============================================================
def filter_chunks(
    cands: list[dict[str, Any]],
    owner_key: str | None = None,
    kinds: list[str] | None = None,
) -> list[dict[str, Any]]:
    """按归属与类型过滤候选块（SQL 取全量后在 Python 侧过滤，便于单测）。"""
    out: list[dict[str, Any]] = []
    kind_set = set(kinds) if kinds else None
    for c in cands:
        if owner_key and c.get("owner_key") != owner_key:
            continue
        if kind_set and c.get("chunk_kind") not in kind_set:
            continue
        out.append(c)
    return out


def rank_candidates(
    query: str,
    cands: list[dict[str, Any]],
    vec_scores: dict[str, float],
    top_k: int = DEFAULT_TOP_K,
) -> tuple[list[dict[str, Any]], str]:
    """混合融合打分：向量 0.7 + BM25 0.3；无向量分则该块只算关键词。

    返回 ``(排序后的候选列表, mode)``，mode ∈ ``hybrid`` / ``keyword_fallback``。
    ``vec_scores`` 为空（本地 embedding 不可用）时整体降级纯 BM25。
    """
    use_vector = bool(vec_scores)
    ranked: list[dict[str, Any]] = []
    for c in cands:
        v = vec_scores.get(c["id"], 0.0)
        k = bm25_score(query, c["content"])
        if use_vector:
            score = VECTOR_WEIGHT * v + KEYWORD_WEIGHT * k
        else:
            score = k
        ranked.append({**c, "score": float(score)})
    ranked.sort(key=lambda x: x["score"], reverse=True)
    mode = "hybrid" if use_vector else "keyword_fallback"
    return ranked[:top_k], mode


# ==============================================================
# 4. 分块采集（DB 读取 + 纯函数组装；evidence 缺失的 kind 静默跳过）
# ==============================================================
async def collect_chunks(
    db: AsyncSession,
    script_id: str,
    video_id: str | None = None,
) -> list[dict[str, Any]]:
    """把一条已拆解 script 的产物组装成分块列表（不含向量，向量在 index_breakdown 算）。

    覆盖 kind：script_summary / segment / hook / sentence_function /
    completion_driver / reusable_element；任一来源缺失只跳过对应 kind，不报错。
    """
    sid = str(script_id)
    chunks: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, int]] = set()

    def _add(ch: dict[str, Any]) -> None:
        key = (ch["chunk_kind"], ch["ref_table"] or "", ch["ref_id"] or "", int(ch["seq"] or 0))
        if key in seen:
            return
        seen.add(key)
        chunks.append(ch)

    # 4.1 script 主行 + 视频标题
    srow = (
        await db.execute(
            text(
                "SELECT id, video_id, core_idea, content_trend, target_audience, "
                "summary, quality_score, duration_ms FROM script_script WHERE id=:sid"
            ),
            {"sid": sid},
        )
    ).mappings().first()
    if srow is None:
        logger.warning("collect_chunks: script_id=%s 不存在，返回空块", sid)
        return []
    script = dict(srow)
    vid = str(script.get("video_id") or video_id or "")

    video_title = ""
    if vid:
        vrow = (
            await db.execute(
                text("SELECT title FROM videos WHERE id=:vid"), {"vid": vid}
            )
        ).fetchone()
        if vrow:
            video_title = vrow[0] or ""

    _add(build_summary_chunk(script, video_title=video_title))

    duration_ms = int(script.get("duration_ms") or 0)

    # 4.2 段落
    segs = (
        await db.execute(
            text(
                "SELECT id, seq, seg_type, title, start_ms, end_ms, purpose, summary, "
                "hook_point, payoff_point FROM script_segment WHERE script_id=:sid ORDER BY seq ASC"
            ),
            {"sid": sid},
        )
    ).mappings().all()
    for seg in segs:
        seg_d = dict(seg)
        _add(build_segment_chunk(seg_d, duration_ms=duration_ms))
        if int(seg_d.get("hook_point") or 0):
            _add(build_segment_hook_chunk(seg_d))

    # 4.3 库级钩子（ref_element_source → lib_hook；script_id / video_id 双兜底关联）
    try:
        hooks = (
            await db.execute(
                text(
                    "SELECT h.* FROM ref_element_source r JOIN lib_hook h "
                    "ON r.element_id = h.id "
                    "WHERE r.element_table='lib_hook' "
                    "AND (r.source_script_id=:sid OR r.source_video_id=:vid)"
                ),
                {"sid": sid, "vid": vid},
            )
        ).mappings().all()
        for h in hooks:
            _add(build_hook_library_chunk(dict(h)))
    except Exception as e:  # noqa: BLE001
        logger.warning("读取库级钩子失败（跳过 hook 库块）：%s", e)

    # 4.4 高价值句子
    sents = (
        await db.execute(
            text(
                "SELECT id, seq, quote, sentence_function, function_reason, start_ms, end_ms, "
                "emotion_intensity, is_hook, is_turn, is_peak "
                "FROM script_sentence WHERE script_id=:sid ORDER BY seq ASC"
            ),
            {"sid": sid},
        )
    ).mappings().all()
    for sent in sents:
        s = dict(sent)
        marked = bool(s.get("is_hook") or s.get("is_turn") or s.get("is_peak"))
        if marked or (s.get("sentence_function") in _VALUABLE_SENTENCE_FUNCTIONS):
            _add(build_sentence_chunk(s))

    # 4.5 完播驱动（evidence.completion_drivers；取不到则跳过该 kind）
    try:
        ev_row = (
            await db.execute(
                text(
                    "SELECT evidence FROM breakdown_jobs WHERE script_id=:sid "
                    "ORDER BY finished_at DESC LIMIT 1"
                ),
                {"sid": sid},
            )
        ).fetchone()
        if ev_row and ev_row[0]:
            ev = ev_row[0]
            if not isinstance(ev, dict):
                ev = json.loads(ev)
            drivers = ev.get("completion_drivers") or []
            for idx, drv in enumerate(drivers):
                if isinstance(drv, dict):
                    _add(build_driver_chunk(drv, idx))
    except (TypeError, ValueError) as e:
        logger.warning("解析 completion_drivers 失败（跳过驱动块）：%s", e)
    except Exception as e:  # noqa: BLE001
        logger.warning("读取 completion_drivers 失败（跳过驱动块）：%s", e)

    # 4.6 可复用积木（method/topic/quote/copywriting，经 ref_element_source 关联本片）
    for tbl in REUSABLE_TABLES:
        try:
            rows = (
                await db.execute(
                    text(
                        f"SELECT e.* FROM ref_element_source r JOIN {tbl} e "
                        "ON r.element_id = e.id "
                        "WHERE r.element_table=:tbl "
                        "AND (r.source_script_id=:sid OR r.source_video_id=:vid)"
                    ),
                    {"tbl": tbl, "sid": sid, "vid": vid},
                )
            ).mappings().all()
            for r in rows:
                _add(build_element_chunk(tbl, dict(r)))
        except Exception as e:  # noqa: BLE001
            logger.warning("读取可复用积木 %s 失败（跳过该表块）：%s", tbl, e)

    # 附带上下文 id（入库用，不进 embedding 文本）
    for ch in chunks:
        ch["video_id"] = vid
        ch["script_id"] = sid
    return chunks


# ==============================================================
# 5. 索引写入：删旧 → hash 复用 → 批量本地 embed → upsert
# ==============================================================
async def index_breakdown(
    db: AsyncSession,
    script_id: str,
    video_id: str | None = None,
    owner_key: str = DEFAULT_OWNER_KEY,
) -> dict[str, int]:
    """为一条 script 重建分块索引。

    流程：采集分块 → 删该 script 旧块 → 文本 hash 未变则复用旧向量（跳过重算）
    → 待算文本批量本地 embed → upsert 入库。返回 ``{chunk_kind: count}``。

    任何异常向上抛（含本地 embedding 不可用），由调用方 try/warning 兜底，
    不影响拆解主流程。
    """
    sid = str(script_id)
    chunks = await collect_chunks(db, sid, video_id)
    counts: dict[str, int] = {}
    if not chunks:
        await db.execute(text("DELETE FROM breakdown_chunk WHERE script_id=:sid"), {"sid": sid})
        await db.commit()
        return counts

    # 5.1 旧块向量快照（hash 命中即复用，避免重复调 Ollama）
    old_rows = (
        await db.execute(
            text(
                "SELECT chunk_kind, ref_table, ref_id, seq, content_hash, embedding, dim "
                "FROM breakdown_chunk WHERE script_id=:sid"
            ),
            {"sid": sid},
        )
    ).all()
    old_map: dict[tuple[str, str, str, int], dict[str, Any]] = {}
    for r in old_rows:
        old_map[(r[0], r[1] or "", str(r[2] or ""), int(r[3] or 0))] = {
            "hash": r[4],
            "embedding": r[5],
            "dim": r[6],
        }

    # 5.2 删旧块（同 script_id 全量重建；owner_key 以本次入参为准）
    await db.execute(text("DELETE FROM breakdown_chunk WHERE script_id=:sid"), {"sid": sid})

    # 5.3 分流：复用向量 vs 待 embed
    pending_texts: list[str] = []
    pending_idx: list[int] = []
    reuse: dict[int, dict[str, Any]] = {}
    for idx, ch in enumerate(chunks):
        h = chunk_content_hash(ch["content"])
        ch["content_hash"] = h
        key = (ch["chunk_kind"], ch["ref_table"] or "", ch["ref_id"] or "", int(ch["seq"] or 0))
        old = old_map.get(key)
        if old and old.get("hash") == h and old.get("embedding"):
            reuse[idx] = {"embedding": old["embedding"], "dim": old.get("dim") or EMBED_DIM}
        else:
            pending_texts.append(ch["content"])
            pending_idx.append(idx)

    # 5.4 批量本地 embed（失败向上抛，由调用方兜底）
    new_vecs: list[list[float]] = []
    if pending_texts:
        new_vecs = await embed_texts(pending_texts)

    # 5.5 upsert 入库
    now = time.time()
    vi = 0
    for idx, ch in enumerate(chunks):
        if idx in reuse:
            blob = reuse[idx]["embedding"]
            dim = int(reuse[idx]["dim"])
        else:
            vec = new_vecs[vi]
            vi += 1
            blob = _vec_to_blob(vec)
            dim = len(vec)

        await db.execute(
            text(
                """
                INSERT INTO breakdown_chunk
                  (id, owner_key, video_id, script_id, chunk_kind, ref_table, ref_id,
                   seq, title, content, content_hash, embedding, dim, model, created_at)
                VALUES
                  (:id, :owner, :vid, :sid, :kind, :rt, :rid, :seq, :title, :content,
                   :hash, :emb, :dim, :model, :now)
                ON CONFLICT(owner_key, chunk_kind, ref_table, ref_id, seq) DO UPDATE SET
                  video_id=excluded.video_id,
                  script_id=excluded.script_id,
                  title=excluded.title,
                  content=excluded.content,
                  content_hash=excluded.content_hash,
                  embedding=excluded.embedding,
                  dim=excluded.dim,
                  model=excluded.model,
                  created_at=excluded.created_at
                """
            ),
            {
                "id": uuid.uuid4().hex,
                "owner": owner_key,
                "vid": ch.get("video_id"),
                "sid": sid,
                "kind": ch["chunk_kind"],
                "rt": ch.get("ref_table"),
                "rid": ch.get("ref_id"),
                "seq": int(ch.get("seq") or 0),
                "title": ch.get("title") or "",
                "content": ch["content"],
                "hash": ch["content_hash"],
                "emb": blob,
                "dim": dim,
                "model": EMBED_MODEL,
                "now": now,
            },
        )
        counts[ch["chunk_kind"]] = counts.get(ch["chunk_kind"], 0) + 1

    await db.commit()
    logger.info(
        "分块索引完成：script_id=%s owner=%s 分块=%d（待算向量 %d 条）",
        sid, owner_key, len(chunks), len(pending_idx),
    )
    return counts


# ==============================================================
# 6. 混合检索：向量 0.7 + BM25 0.3；向量不可用降级纯关键词
# ==============================================================
async def search_chunks(
    db: AsyncSession,
    query: str,
    owner_key: str | None = None,
    top_k: int = DEFAULT_TOP_K,
    kinds: list[str] | None = None,
) -> dict[str, Any]:
    """混合检索拆解分块，返回 ``{items: [...], mode: hybrid|keyword_fallback}``。

    item 字段：chunk_kind / title / content(截断) / score / video_id / script_id /
    ref_table / ref_id / url / platform / video_title，便于下一阶段 chat citations 直接复用。
    空 query 或无结果均返回空 items，不报错。
    """
    q = (query or "").strip()
    if not q:
        return {"items": [], "mode": "keyword_fallback"}

    rows = (
        await db.execute(
            text(
                "SELECT id, owner_key, video_id, script_id, chunk_kind, ref_table, ref_id, "
                "title, content, embedding, dim FROM breakdown_chunk"
            )
        )
    ).all()
    if not rows:
        return {"items": [], "mode": "hybrid"}

    cands: list[dict[str, Any]] = []
    for r in rows:
        cands.append(
            {
                "id": r[0],
                "owner_key": r[1],
                "video_id": r[2],
                "script_id": r[3],
                "chunk_kind": r[4],
                "ref_table": r[5],
                "ref_id": r[6],
                "title": r[7],
                "content": r[8] or "",
                "_embedding": r[9],
                "_dim": r[10],
            }
        )
    cands = filter_chunks(cands, owner_key=owner_key, kinds=kinds)
    if not cands:
        return {"items": [], "mode": "hybrid"}

    # 6.1 向量分（失败降级：vec_scores 留空，rank_candidates 自动走纯 BM25）
    vec_scores: dict[str, float] = {}
    vector_ok = False
    try:
        qvec_list = await embed_texts([q])
        qvec = np.asarray(qvec_list[0], dtype=np.float32)
        qvec = qvec / (np.linalg.norm(qvec) + 1e-10)
        emb_cands = [c for c in cands if c["_dim"] == EMBED_DIM and c["_embedding"]]
        if emb_cands:
            mat = np.stack([_blob_to_vec(c["_embedding"]) for c in emb_cands])
            norms = np.linalg.norm(mat, axis=1, keepdims=True) + 1e-10
            scores = (mat / norms) @ qvec
            for c, s in zip(emb_cands, scores):
                vec_scores[c["id"]] = float(s)
        vector_ok = True
    except Exception as e:  # noqa: BLE001
        logger.warning("分块向量检索失败（降级纯 BM25）：%s", e)
        vector_ok = False

    ranked, _mode = rank_candidates(q, cands, vec_scores, top_k=top_k)
    mode = "hybrid" if vector_ok else "keyword_fallback"

    # 6.2 补 videos.url / platform / title
    vid_ids = sorted({c["video_id"] for c in ranked if c.get("video_id")})
    video_meta: dict[str, dict[str, Any]] = {}
    if vid_ids:
        ph = ",".join(f":v{i}" for i in range(len(vid_ids)))
        vrows = (
            await db.execute(
                text(f"SELECT id, url, platform, title FROM videos WHERE id IN ({ph})"),
                {f"v{i}": v for i, v in enumerate(vid_ids)},
            )
        ).all()
        for vr in vrows:
            video_meta[str(vr[0])] = {"url": vr[1], "platform": vr[2], "video_title": vr[3]}

    items: list[dict[str, Any]] = []
    for c in ranked:
        meta = video_meta.get(c["video_id"], {})
        items.append(
            {
                "chunk_kind": c["chunk_kind"],
                "title": c.get("title") or "",
                "content": (c.get("content") or "")[:CONTENT_PREVIEW_LIMIT],
                "score": round(c["score"], 4),
                "video_id": c["video_id"],
                "script_id": c["script_id"],
                "ref_table": c["ref_table"],
                "ref_id": c["ref_id"],
                "url": meta.get("url"),
                "platform": meta.get("platform"),
                "video_title": meta.get("video_title"),
            }
        )
    return {"items": items, "mode": mode}
