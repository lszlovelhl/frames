"""元素库统一读写层（三层分库积木库 + AI 组合产物）

旧表 ``elements`` / ``element_versions`` 已随第 7 章清空整体 DROP，元素来源改为
三层分库的第三层积木库（lib_*）：

======================  ========  ==============================
表                     元素分类   说明
======================  ========  ==============================
lib_topic              选题       选题角度 / 人群 / 痛点 / 价值类型
lib_hook               钩子       钩型 / 句式模板 / 心理学机制
lib_copywriting        话术       口播·字幕·标题·CTA / 修辞
lib_quote              话术       金句原话 / 结构 / 改写模板
lib_method             剪辑手法   方法论（叙事/结构/修辞/视听/节奏…）
lib_combo              结构       情绪结构配方（序列 + 情绪形状）
lib_mix_draft          综合       AI 组合/变异产物（待质控草稿）
======================  ========  ==============================

元素 id 采用 ``"{table}:{uuid}"`` 复合格式（如 ``lib_hook:2f1c…``），等价于旧
``elements.id``；质控四态（draft/accepted/adjusted/rejected）承载在 ``review_status``
列上，语义与调用方完全一致。
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as M

logger = logging.getLogger(__name__)

# 积木库表 → 前端元素分类
LIB_TABLES: dict[str, str] = {
    "lib_topic": "选题",
    "lib_hook": "钩子",
    "lib_copywriting": "话术",
    "lib_quote": "话术",
    "lib_method": "剪辑手法",
    "lib_combo": "结构",
}
MIX_TABLE = "lib_mix_draft"

STATUS_ORDER = {"draft": 0, "adjusted": 1, "accepted": 2, "rejected": 3}
REVIEW_ACTIONS = {"accept": "accepted", "reject": "rejected", "adjust": "adjusted"}

# 方法论库的二级分类 → 前端元素分类（叙事/结构/修辞归"结构"，视听归"视觉"）
_METHOD_CATEGORY = {
    "叙事": "结构",
    "结构": "结构",
    "修辞": "结构",
    "视听": "视觉",
    "节奏": "剪辑手法",
    "互动": "运营策略",
    "运营": "运营策略",
}

# 关键词检索覆盖的列（避免各表字段差异外溢到路由层）
_SEARCH_COLUMNS: dict[str, tuple[str, ...]] = {
    "lib_topic": ("name", "angle", "pain_point", "audience", "mechanism"),
    "lib_hook": ("name", "sentence_pattern", "expected_effect", "mechanism"),
    "lib_copywriting": ("name", "sentence_pattern", "example_text", "mechanism"),
    "lib_quote": ("text", "structure", "rewrite_template", "applicable_scene"),
    "lib_method": ("name", "mechanism", "usage_steps", "controlled_tag", "category"),
    "lib_combo": ("name", "sequence_desc", "mechanism", "content_trend"),
    MIX_TABLE: ("name", "description", "formula"),
}

# id 前缀既接受新格式 "lib_hook:uuid"，也兼容裸露 uuid（全表回退查找）
MAX_ROWS_PER_TABLE = 2000


def models() -> dict[str, type]:
    """表名 → ORM 模型（含 AI 产物表）。"""
    return {
        "lib_topic": M.LibTopic,
        "lib_hook": M.LibHook,
        "lib_copywriting": M.LibCopywriting,
        "lib_quote": M.LibQuote,
        "lib_method": M.LibMethod,
        "lib_combo": M.LibCombo,
        MIX_TABLE: M.LibMixDraft,
    }


def parse_element_id(raw: str) -> tuple[str, UUID]:
    """解析元素 id → (表名, uuid)；裸露 uuid 返回 ("", uuid) 交由全表回退查找。"""
    text = (raw or "").strip()
    if ":" in text:
        table, _, tail = text.partition(":")
        if table not in LIB_TABLES and table != MIX_TABLE:
            raise ValueError(f"未知元素表：{table}")
        return table, UUID(tail)
    return "", UUID(text)


def element_id(table: str, row_id) -> str:
    return f"{table}:{row_id}"


# ---------------------------------------------------------------- 字段归一化

def _row_name(table: str, r) -> str:
    if table == "lib_quote":
        return (r.text or "")[:40]
    if table == MIX_TABLE:
        return r.name or ""
    return r.name or r.code or ""


def _row_description(table: str, r) -> str:
    if table == "lib_topic":
        hits = [f"人群：{r.audience}", f"痛点：{r.pain_point}", f"角度：{r.angle}"]
        return "；".join(x for x in hits if x and not x.endswith("："))
    if table == "lib_hook":
        return f"钩型：{r.hook_type}；预期效果：{r.expected_effect}"
    if table == "lib_copywriting":
        return f"文案类型：{r.copy_type}；修辞：{'、'.join(r.rhetoric or [])}"
    if table == "lib_quote":
        return f"句法结构：{r.structure}；适用场景：{r.applicable_scene}"
    if table == "lib_method":
        return f"方法论分类：{r.category}；受控标签：{r.controlled_tag}"
    if table == "lib_combo":
        return f"创作意图：{r.intent}；情绪形状：{r.emotion_shape}"
    return r.description or ""


def _row_formula(table: str, r) -> str:
    if table == "lib_topic":
        return r.angle or r.mechanism or ""
    if table == "lib_hook":
        return r.sentence_pattern or r.mechanism or ""
    if table == "lib_copywriting":
        return r.sentence_pattern or r.example_text or r.mechanism or ""
    if table == "lib_quote":
        return r.rewrite_template or r.mechanism or ""
    if table == "lib_method":
        return r.usage_steps or r.mechanism or ""
    if table == "lib_combo":
        return r.sequence_desc or r.emotion_shape or r.mechanism or ""
    return r.formula or ""


def _row_category(table: str, r) -> str:
    if table == "lib_method":
        return _METHOD_CATEGORY.get(r.category or "", "剪辑手法")
    if table == MIX_TABLE:
        return r.category or "综合"
    return LIB_TABLES.get(table, "综合")


def _row_tags(table: str, r) -> list[str]:
    if table == "lib_topic":
        return [x for x in [r.topic_type, r.value_type] if x] + list(r.keywords or [])
    if table == "lib_hook":
        return [x for x in [r.hook_type, r.position] if x]
    if table == "lib_copywriting":
        return [x for x in [r.copy_type] if x] + list(r.rhetoric or [])
    if table == "lib_quote":
        return [x for x in [r.applicable_scene] if x]
    if table == "lib_method":
        return [x for x in [r.category, r.controlled_tag] if x]
    if table == "lib_combo":
        return [x for x in [r.intent, r.emotion_shape] if x]
    return list(r.tags or [])


def _row_confidence(r) -> float | None:
    score = getattr(r, "quality_score", None)
    if score is None:
        return None
    return round(float(score) / 100, 4)


def _row_updated_at(r) -> str | None:
    ts = getattr(r, "updated_at", None) or getattr(r, "created_at", None)
    return ts.isoformat() if ts else None


def _row_status(r) -> str:
    return getattr(r, "review_status", None) or "draft"


def serialize(
    table: str,
    r,
    *,
    evidence: list[dict] | None = None,
    video: dict | None = None,
    job_id: str | None = None,
    usage_count: int = 0,
    slots: list[dict] | None = None,
) -> dict:
    """统一序列化为前端 ElementItem 形状（字段名与旧 elements 接口一致）。"""
    return {
        "id": element_id(table, r.id),
        "analysis_id": job_id,
        "usage_count": usage_count,
        "category": _row_category(table, r),
        "name": _row_name(table, r)[:100],
        "description": _row_description(table, r) or None,
        "formula": _row_formula(table, r) or None,
        "source_type": "extracted" if table != MIX_TABLE else "combo",
        "confidence": _row_confidence(r),
        "role_view": getattr(r, "role_view", None) or "编导",
        "evidence": evidence or [],
        "status": _row_status(r),
        "tags": _row_tags(table, r),
        "created_at": r.created_at.isoformat() if getattr(r, "created_at", None) else None,
        "updated_at": _row_updated_at(r),
        "video": video,
        # 三层分库专属：来源表 + 来源脚本，供创作链路与调试使用
        "element_table": table,
        # 组合模板专属：槽位序列（手法/位置/时长/角色/可替换项），供前端"拿来就改"呈现
        "slots": slots or [],
    }


# ---------------------------------------------------------------- 查询

async def _evidence_map(db: AsyncSession, pairs: list[tuple[str, UUID]]) -> dict[str, list[dict]]:
    """元素 → 原片证据（段落序号 + 原话），来自 ref_element_source 溯源表。"""
    if not pairs:
        return {}
    ids = [pid for _, pid in pairs]
    refs = (
        (
            await db.execute(
                select(M.RefElementSource)
                .where(M.RefElementSource.element_id.in_(ids))
                .order_by(M.RefElementSource.extracted_at.asc())
            )
        )
        .scalars()
        .all()
    )
    if not refs:
        return {}
    seg_ids = {r.source_segment_id for r in refs if r.source_segment_id}
    seg_seq: dict[UUID, int] = {}
    if seg_ids:
        rows = (
            await db.execute(
                select(M.ScriptSegment.id, M.ScriptSegment.seq).where(
                    M.ScriptSegment.id.in_(seg_ids)
                )
            )
        ).all()
        seg_seq = {sid: seq for sid, seq in rows}
    out: dict[str, list[dict]] = {}
    for r in refs:
        key = element_id(r.element_table, r.element_id)
        bucket = out.setdefault(key, [])
        if len(bucket) >= 3:
            continue
        item: dict = {}
        if r.source_segment_id and r.source_segment_id in seg_seq:
            item["seg"] = seg_seq[r.source_segment_id]
        if r.quote:
            item["quote"] = r.quote[:120]
        if item:
            bucket.append(item)
    return out


async def _video_map(db: AsyncSession, pairs: list[tuple[str, UUID]]) -> dict[str, dict]:
    refs = (
        (
            await db.execute(
                select(M.RefElementSource.element_table, M.RefElementSource.element_id, M.RefElementSource.source_video_id)
                .where(M.RefElementSource.element_id.in_([p for _, p in pairs]))
            )
        )
        .all()
    ) if pairs else []
    if not refs:
        return {}
    video_ids = {r[2] for r in refs}
    videos = (
        (
            await db.execute(
                select(
                    M.Video.id,
                    M.Video.title,
                    M.Video.platform,
                    M.Video.author_name,
                    M.Video.category_guess,
                ).where(M.Video.id.in_(video_ids))
            )
        )
        .all()
    )
    vmap = {
        vid: {
            "id": str(vid),
            "title": title,
            "platform": platform,
            "author_name": author,
            "category_guess": cat,
        }
        for vid, title, platform, author, cat in videos
    }
    # 元素 → 来源视频取最早一条溯源
    out: dict[str, dict] = {}
    for table, eid, vid in refs:
        key = element_id(table, eid)
        if key in out:
            continue
        info = vmap.get(vid)
        if info:
            out[key] = info
    return out


async def _job_map(db: AsyncSession, videos: dict[str, dict]) -> dict[str, str]:
    """来源视频 → 最近一次拆解任务 id（回填 ElementItem.analysis_id）。"""
    vids = {UUID(v["id"]) for v in videos.values()}
    if not vids:
        return {}
    rows = (
        (
            await db.execute(
                select(M.BreakdownJob.video_id, M.BreakdownJob.id, M.BreakdownJob.created_at)
                .where(M.BreakdownJob.video_id.in_(vids))
                .order_by(M.BreakdownJob.created_at.desc())
            )
        )
        .all()
    )
    out: dict[str, str] = {}
    for vid, jid, _ in rows:
        out.setdefault(str(vid), str(jid))
    return out


async def usage_counts(db: AsyncSession) -> dict[str, int]:
    """创作回流：每个元素被多少个创作项目引用（Creation.core_elements）。"""
    counts: dict[str, int] = {}
    rows = await db.execute(select(M.Creation.core_elements))
    for (arr,) in rows.all():
        if not arr:
            continue
        seen: set[str] = set()
        for item in arr:
            if not isinstance(item, dict):
                continue
            eid = item.get("element_id")
            if eid and eid not in seen:
                seen.add(eid)
                counts[eid] = counts.get(eid, 0) + 1
    return counts


async def _combo_slots_map(db: AsyncSession, pairs: list[tuple[str, UUID]]) -> dict[str, list[dict]]:
    """组合模板槽位：lib_combo 元素 → 槽位序列（手法/区间/角色/时长/可替换项）。"""
    combo_ids = [pid for name, pid in pairs if name == "lib_combo"]
    if not combo_ids:
        return {}
    slots = (
        (
            await db.execute(
                select(M.LibComboSlot)
                .where(M.LibComboSlot.combo_id.in_(combo_ids))
                .order_by(M.LibComboSlot.combo_id, M.LibComboSlot.seq)
            )
        )
        .scalars()
        .all()
    )
    out: dict[str, list[dict]] = {}
    for s in slots:
        out.setdefault(str(s.combo_id), []).append(
            {
                "seq": s.seq,
                "slot_role": s.slot_role,
                "method_code": s.method_code,
                "position_ratio_start": s.position_ratio_start,
                "position_ratio_end": s.position_ratio_end,
                "duration_ratio": s.duration_ratio,
                "expected_function": s.expected_function,
                "swap_alternatives": s.swap_alternatives or [],
            }
        )
    return out


async def list_elements(
    db: AsyncSession,
    *,
    status: str | None = None,
    category: str | None = None,
    q: str | None = None,
    table: str | None = None,
    limit: int = 300,
) -> dict:
    """跨积木库聚合检索：状态 / 分类 / 关键词过滤，未质控优先。"""
    registry = models()
    tables = [table] if table else list(registry)
    kw = (q or "").strip().lower()
    picked: list[tuple[str, object]] = []
    status_counts: dict[str, int] = {}

    for name in tables:
        model = registry[name]
        stmt = select(model).order_by(model.created_at.desc()).limit(MAX_ROWS_PER_TABLE)
        rows = (await db.execute(stmt)).scalars().all()
        for r in rows:
            st = _row_status(r)
            if status and status != "all" and st != status:
                continue
            status_counts[st] = status_counts.get(st, 0) + 1
            if category and category != "all" and _row_category(name, r) != category:
                continue
            if kw:
                hay = " ".join(
                    str(getattr(r, col, "") or "")
                    for col in _SEARCH_COLUMNS.get(name, ("name",))
                ).lower()
                if kw not in hay:
                    continue
            picked.append((name, r))

    def _rank(item: tuple[str, object]) -> tuple[int, float]:
        ts = _row_updated_at(item[1])
        stamp = 0.0
        if ts:
            try:
                stamp = datetime.fromisoformat(ts).timestamp()
            except ValueError:
                stamp = 0.0
        return (STATUS_ORDER.get(_row_status(item[1]), 4), -stamp)

    picked.sort(key=_rank)
    total = len(picked)
    picked = picked[:limit]

    pairs = [(name, r.id) for name, r in picked]
    evidence = await _evidence_map(db, pairs)
    videos = await _video_map(db, pairs)
    jobs = await _job_map(db, videos)
    usage = await usage_counts(db)
    combo_slots = await _combo_slots_map(db, pairs)

    items = []
    for name, r in picked:
        eid = element_id(name, r.id)
        video = videos.get(eid)
        items.append(
            serialize(
                name,
                r,
                evidence=evidence.get(eid, []),
                video=video,
                job_id=jobs.get(video["id"]) if video else None,
                usage_count=usage.get(eid, 0),
                slots=combo_slots.get(str(r.id), []),
            )
        )
    return {"total": total, "status_counts": status_counts, "items": items}


async def load_elements(db: AsyncSession, ids: list[str]) -> list[dict]:
    """按复合 id 批量装载元素（创作链路注入用，保持传入顺序）。"""
    registry = models()
    resolved: list[tuple[str, UUID]] = []
    for raw in ids or []:
        try:
            table, uid = parse_element_id(raw)
        except (ValueError, TypeError):
            continue
        resolved.append((table, uid))
    if not resolved:
        return []

    buckets: dict[str, list[UUID]] = {}
    for table, uid in resolved:
        names = [table] if table else list(registry)
        for name in names:
            buckets.setdefault(name, []).append(uid)

    found: dict[tuple[str, UUID], object] = {}
    for name, uids in buckets.items():
        if not uids:
            continue
        rows = (
            (await db.execute(select(registry[name]).where(registry[name].id.in_(uids))))
            .scalars()
            .all()
        )
        for r in rows:
            found[(name, r.id)] = r

    pairs = [(name, uid) for name, uid in resolved if (name, uid) in found]
    evidence = await _evidence_map(db, pairs)
    videos = await _video_map(db, pairs)
    jobs = await _job_map(db, videos)
    combo_slots = await _combo_slots_map(db, pairs)

    out: list[dict] = []
    for table, uid in resolved:
        key = (table, uid)
        if key not in found:
            # 裸露 uuid：回退命中的表
            hit = next((k for k in found if k[1] == uid), None)
            if hit is None:
                continue
            key = hit
        name, rid = key
        eid = element_id(name, rid)
        video = videos.get(eid)
        out.append(
            serialize(
                name,
                found[key],
                evidence=evidence.get(eid, []),
                video=video,
                job_id=jobs.get(video["id"]) if video else None,
                slots=combo_slots.get(str(rid), []),
            )
        )
    return out


async def review_element(
    db: AsyncSession, element_id_raw: str, action: str, patch: dict | None
) -> dict:
    """质控四态流转：accept/reject/adjust（adjust 支持改名/描述/配方）。"""
    registry = models()
    if action not in REVIEW_ACTIONS:
        raise ValueError(f"不支持的动作：{action}")
    table, uid = parse_element_id(element_id_raw)
    names = [table] if table else list(registry)
    row = None
    hit_table = table
    for name in names:
        row = (
            await db.execute(select(registry[name]).where(registry[name].id == uid))
        ).scalar_one_or_none()
        if row is not None:
            hit_table = name
            break
    if row is None:
        raise LookupError("元素不存在")

    row.review_status = REVIEW_ACTIONS[action]
    if action == "adjust" and patch:
        name_val = patch.get("name")
        if isinstance(name_val, str) and name_val.strip():
            if hit_table == "lib_quote":
                row.text = name_val.strip()
            else:
                row.name = name_val.strip()[:128]
        if isinstance(patch.get("description"), str) and patch["description"].strip():
            if hit_table == MIX_TABLE:
                row.description = patch["description"].strip()
            else:
                for col in ("mechanism", "expected_effect", "usage_steps"):
                    if hasattr(row, col):
                        setattr(row, col, patch["description"].strip())
                        break
        if isinstance(patch.get("formula"), str) and patch["formula"].strip():
            for col in ("sentence_pattern", "rewrite_template", "angle", "sequence_desc", "formula"):
                if hasattr(row, col):
                    setattr(row, col, patch["formula"].strip())
                    break
        if isinstance(patch.get("category"), str) and patch["category"].strip():
            if hasattr(row, "category") and hit_table in ("lib_method", MIX_TABLE):
                row.category = patch["category"].strip()[:64]
    await db.commit()
    return {"id": element_id(hit_table, row.id), "status": row.review_status}


async def create_mix_drafts(db: AsyncSession, items: list[dict], *, mode: str, source_names: list[str]) -> list[dict]:
    """AI 组合/变异产物落库（lib_mix_draft，draft 待质控）。"""
    created: list[M.LibMixDraft] = []
    tags = ["ai_mix", f"mode:{mode}", f"源自:{'; '.join(source_names)}"]
    for item in items:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        desc = str(item.get("description") or "").strip()
        formula = str(item.get("formula") or "").strip()
        if not desc and not formula:
            continue
        row = M.LibMixDraft(
            category=(str(item.get("category") or "综合").strip() or "综合")[:32],
            name=name[:128],
            description=desc[:1000],
            formula=formula[:2000],
            source_type="variant" if mode == "vary" else "combo",
            tags=tags,
            review_status="draft",
            quality_score=None,
            role_view="编导",
        )
        db.add(row)
        created.append(row)
    await db.flush()
    await db.commit()
    return [
        serialize(
            MIX_TABLE,
            r,
            evidence=[],
            video=None,
            job_id=None,
        )
        for r in created
    ]
