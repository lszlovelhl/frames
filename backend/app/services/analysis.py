"""拆解编排服务（唯一链路：三层物理分库）

第 7 章清空已整体 DROP A 级旧表（analyses / analysis_layers / segments /
analysis_notes / elements / element_versions / annotations / category_templates），
本模块完成应用层迁移：

* 队列 + 进度 + 证据 → ``breakdown_jobs``（替代旧 analyses）；
* 拆解产物 → 三层分库新表 raw_*/script_*/lib_*/ref_*（由 services.three_layer 写入）；
* 结果组装（``analysis_result``）→ 从 script_script / script_segment / script_sentence /
  script_emotion_curve / script_turn_point / ref_element_source 现场聚合，
  对外仍输出旧前端契约的 L1~L5 结构，前端无需改动即可正常渲染。

``load_active_prompt`` 在此模块再次导出：三层 pipeline 反向依赖该符号，保持兼容。
"""
import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as M
from app.services.prompt_store import load_active_prompt  # noqa: F401  (pipeline 反向复用)
from app.services.zh import has_traditional, simplify_obj

logger = logging.getLogger(__name__)

TARGET_LAYERS = 5
ACTIVE_STATUSES = ("queued", "running")
SETTLED_STATUSES = ("done", "partial", "failed")

# 写入 breakdown_jobs.evidence 的三层链路关键字段（JSON 列，不做 UUID 直存）
THREE_LAYER_EVIDENCE_KEYS = (
    "ok",
    "script_id",
    "sentence_count",
    "segment_count",
    "turn_point_count",
    "raw_counts",
    "lib_counts",
    "validation",
    "warnings",
    "quality",
    "curve",
    "element_baseline",
    "error",
)

LAYER_LABELS = {
    1: "L1 建档预判",
    2: "L2 节奏快照",
    3: "L3 结构拆解",
    4: "L4 精修诊断",
    5: "L5 元素抽取",
}


def _now() -> datetime:
    return datetime.now(UTC)


# ------------------------------------------------------------------ 任务生命周期

async def create_job(db: AsyncSession, video_id, *, model: str | None = None) -> M.BreakdownJob:
    """新建一条拆解任务（queued），队列与进度统一由本表承载。"""
    job = M.BreakdownJob(video_id=video_id, status="queued", model=model)
    db.add(job)
    await db.flush()
    return job


async def active_job(db: AsyncSession, video_id) -> M.BreakdownJob | None:
    """该视频正在排队/运行的任务（有则不允许重复发起）。"""
    return (
        await db.execute(
            select(M.BreakdownJob)
            .where(
                M.BreakdownJob.video_id == video_id,
                M.BreakdownJob.status.in_(ACTIVE_STATUSES),
            )
            .order_by(M.BreakdownJob.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def latest_job(db: AsyncSession, video_id) -> M.BreakdownJob | None:
    return (
        await db.execute(
            select(M.BreakdownJob)
            .where(M.BreakdownJob.video_id == video_id)
            .order_by(M.BreakdownJob.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def resumable_job(db: AsyncSession, video_id) -> M.BreakdownJob | None:
    """最近一次中断/失败/部分完成的任务（可续跑）。"""
    return (
        await db.execute(
            select(M.BreakdownJob)
            .where(
                M.BreakdownJob.video_id == video_id,
                M.BreakdownJob.status.in_(("partial", "failed")),
            )
            .order_by(M.BreakdownJob.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def set_progress(
    db: AsyncSession,
    job: M.BreakdownJob,
    *,
    stage: str,
    message: str,
    pct: int | None = None,
    layer: int | None = None,  # 兼容旧调用签名；三层链路用 stage 表达进度
    commit: bool = True,
) -> None:
    """进度常驻：写 breakdown_jobs.stage/message/pct，前端轮询 /api/analyses/active。"""
    job.stage = (stage or "")[:32]
    job.message = message or ""
    if pct is not None:
        job.pct = max(0, min(int(pct), 100))
    if commit:
        await db.commit()


async def mark_done(
    db: AsyncSession,
    job: M.BreakdownJob,
    *,
    evidence: dict | None = None,
    script_id=None,
    status: str = "done",
) -> None:
    job.status = status
    job.pct = 100
    job.finished_at = _now()
    if script_id is not None:
        job.script_id = script_id
    if evidence is not None:
        job.evidence = json.loads(json.dumps(evidence, ensure_ascii=False, default=str))


async def mark_failed(db: AsyncSession, job: M.BreakdownJob, *, message: str) -> None:
    job.status = "failed"
    job.pct = 100
    job.stage = "failed"
    job.message = message
    job.failed_reason = message
    job.finished_at = _now()


# ------------------------------------------------------------------ 三层拆解

async def run_three_layer_breakdown(
    db: AsyncSession,
    video: M.Video,
    job: M.BreakdownJob,
    *,
    manifest: dict | None = None,
    model: str = "flash",
) -> dict[str, Any]:
    """唯一拆解链路：调 services.three_layer 写三层分库（raw_*/script_*/lib_*/ref_*）。

    职责：幂等 seed 三层提示词 → 执行三层链路 → 证据落 breakdown_jobs.evidence。
    失败不抛出：异常记 warning 并落 evidence["error"]，由调用方按 job.status 呈现，
    绝不触碰、删除任何既有数据。
    """
    # 延迟导入：three_layer.pipeline 反向依赖本模块的 load_active_prompt，顶层导入成环
    from app.services.three_layer.pipeline import run_three_layer
    from app.services.three_layer.seed_prompts import seed_three_layer_prompts

    seeded: list[dict[str, Any]] = []
    try:
        seeded = await seed_three_layer_prompts(db)
        await db.commit()
    except Exception:  # noqa: BLE001
        logger.warning("三层提示词 seed 失败（继续执行拆解）", exc_info=True)
        await db.rollback()

    async def progress(stage: str, pct: int, msg: str) -> None:
        await set_progress(db, job, stage=f"three_{stage}", message=msg, pct=pct)

    try:
        result = await run_three_layer(
            db,
            video,
            analysis_id=str(job.id),
            manifest=manifest,
            model=model,
            progress=progress,
        )
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("三层分库拆解失败：video=%s", video.id, exc_info=True)
        await db.rollback()
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    evidence = {k: result[k] for k in THREE_LAYER_EVIDENCE_KEYS if k in result}
    validation = result.get("validation") or {}
    if validation:
        # 只留统计口径与少量样例，避免 evidence 膨胀
        evidence["validation"] = {
            "total_rows": validation.get("total_rows"),
            "active": validation.get("active"),
            "draft": validation.get("draft"),
            "rejected": validation.get("rejected"),
            "soft_total": validation.get("soft_total"),
            "rejected_rows": (validation.get("rejected_rows") or [])[:5],
            "draft_rows": (validation.get("draft_rows") or [])[:5],
        }
    evidence["seeded_prompts"] = [
        {"code": s.get("code"), "action": s.get("action"), "version": s.get("version")}
        for s in seeded
    ]
    try:
        await mark_done(
            db,
            job,
            evidence=evidence,
            script_id=result.get("script_id"),
            status="done" if result.get("ok") else "failed",
        )
        if not result.get("ok"):
            job.failed_reason = str(result.get("error") or "三层拆解失败")[:1000]
        await db.commit()
    except Exception:  # noqa: BLE001
        logger.warning("三层拆解证据写入 breakdown_jobs 失败", exc_info=True)
        await db.rollback()
    return result


# ------------------------------------------------------------------ 结果组装

async def _script_of(db: AsyncSession, video_id) -> M.ScriptScript | None:
    return (
        await db.execute(
            select(M.ScriptScript)
            .where(M.ScriptScript.video_id == video_id)
            .order_by(M.ScriptScript.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def _clean_text(value: Any) -> str:
    text = "" if value is None else str(value)
    if text and has_traditional(text):
        text = str(simplify_obj(text))
    return text


async def analysis_result(db: AsyncSession, job: M.BreakdownJob) -> dict:
    """组装一次拆解的完整结果（旧前端 L1~L5 契约 ← 三层分库新表现场聚合）。"""
    video = (
        await db.execute(select(M.Video).where(M.Video.id == job.video_id))
    ).scalar_one_or_none()
    script = await _script_of(db, job.video_id) if video else None
    evidence = job.evidence or {}

    if script is None:
        return {
            "id": str(job.id),
            "video_id": str(job.video_id) if job.video_id else None,
            "status": job.status,
            "current_layer": 0,
            "ai_confidence": None,
            "reviewed_by_user": False,
            "summary": {},
            "meta": {"three_layer": evidence, "progress": _progress_dict(job)},
            "layers": [],
            "video": _video_public(video) if video else None,
        }

    segments = (
        (
            await db.execute(
                select(M.ScriptSegment)
                .where(M.ScriptSegment.script_id == script.id)
                .order_by(M.ScriptSegment.seq.asc())
            )
        )
        .scalars()
        .all()
    )
    sentences = (
        (
            await db.execute(
                select(M.ScriptSentence)
                .where(M.ScriptSentence.script_id == script.id)
                .order_by(M.ScriptSentence.seq.asc())
            )
        )
        .scalars()
        .all()
    )
    curve = (
        await db.execute(
            select(M.ScriptEmotionCurve).where(M.ScriptEmotionCurve.script_id == script.id)
        )
    ).scalar_one_or_none()
    turns = (
        (
            await db.execute(
                select(M.ScriptTurnPoint)
                .where(M.ScriptTurnPoint.script_id == script.id)
                .order_by(M.ScriptTurnPoint.seq.asc())
            )
        )
        .scalars()
        .all()
    )

    elements = await _element_cards(db, script, segments, sentences)
    confidence = (
        round(float(script.quality_score) / 100, 4)
        if script.quality_score is not None
        else None
    )
    model = job.model
    layers = [
        _layer1(script, segments, sentences, elements, model),
        _layer2(script, segments, curve, turns, sentences, model),
        _layer3(segments, model),
        _layer4(sentences, turns, model),
        _layer5(elements, model),
    ]
    summary = {
        "one_liner": _clean_text(script.summary or script.core_idea),
        "topic": _clean_text(script.category or script.title or ""),
        "hook_hypothesis": _hook_hypothesis(sentences),
        "target_audience": _clean_text(script.target_audience),
        "key_perspective": _clean_text(script.core_idea),
    }
    return {
        "id": str(job.id),
        "video_id": str(job.video_id),
        "status": job.status,
        "current_layer": len(segments) and len(layers) or 0,
        "ai_confidence": confidence,
        "reviewed_by_user": False,
        "summary": summary,
        "meta": {
            "three_layer": evidence,
            "progress": _progress_dict(job),
            "model": model,
            "script_id": str(script.id),
            "sentence_count": len(sentences),
            "segment_count": len(segments),
        },
        "layers": layers,
        "video": _video_public(video) if video else None,
    }


def _progress_dict(job: M.BreakdownJob) -> dict:
    return {
        "stage": job.stage or None,
        "message": job.message or "",
        "layer": None,
        "pct": job.pct or (100 if job.status in SETTLED_STATUSES else 0),
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }


def _video_public(video: M.Video) -> dict:
    return {
        "id": str(video.id),
        "title": video.title,
        "platform": video.platform,
        "cover_url": video.cover_url,
        "duration_ms": video.duration_ms,
        "author_name": video.author_name,
        "category_guess": video.category_guess,
    }


def _hook_hypothesis(sentences: list[M.ScriptSentence]) -> str:
    for s in sentences:
        if s.is_hook:
            return f"{_clean_text(s.quote)}（{_clean_text(s.function_reason)}）"
    for s in sentences:
        if s.sentence_function == "钩子":
            return _clean_text(s.quote)
    return ""


def _layer1(script, segments, sentences, elements, model) -> dict:
    content = {
        "one_liner": _clean_text(script.summary or script.core_idea),
        "topic": _clean_text(script.category or script.title or ""),
        "hook_hypothesis": _hook_hypothesis(sentences),
        "expected_structure": [
            _clean_text(s.seg_type) for s in segments[:7]
        ] or ["待补充"],
        "target_audience": _clean_text(script.target_audience),
        "key_perspective": _clean_text(script.core_idea),
        "content_trend": _clean_text(script.content_trend),
    }
    return _layer("main", 1, "编导", content, script.quality_score, model)


def _curve_pairs(curve) -> list[tuple[int, float]]:
    """把 intensity_series 归一为 [(t_ms, intensity), ...]。

    三层链路落库口径是 ``[[t_ms, intensity], ...]``（见 three_layer/prompts.py 契约），
    旧版遗留口径是纯数值 ``[v, ...]``。此处一并兼容：是二元组就取时间锚+强度，
    是裸数值就按 sample_interval_ms 推算时间锚，避免下游 float() 直接吃到 list。
    """
    if curve is None:
        return []
    interval = int(curve.sample_interval_ms or 0)
    out: list[tuple[int, float]] = []
    for i, item in enumerate(curve.intensity_series or []):
        if isinstance(item, (list, tuple)):
            t_ms = int(item[0]) if len(item) > 1 else i * interval
            raw = item[-1] if len(item) > 1 else item[0]
        else:
            t_ms, raw = i * interval, item
        try:
            out.append((t_ms, float(raw)))
        except (TypeError, ValueError):
            continue
    return out


def _layer2(script, segments, curve, turns, sentences, model) -> dict:
    pairs = _curve_pairs(curve)
    # 等间隔采样 → 12 个展示点（保留原始时间锚与所属段落，避免丢时间锚）
    step = max(1, len(pairs) // 12) if pairs else 1
    points = []
    for i in range(0, len(pairs), step):
        t_ms, level = pairs[i]
        seg = next((s for s in segments if s.start_ms <= t_ms <= max(s.end_ms, s.start_ms)), None)
        points.append(
            {
                "phase": _clean_text(seg.seg_type) if seg else f"采样{i + 1}",
                "level": round(level, 2),
                "t_ms": t_ms,
            }
        )
    hook_count = sum(1 for s in sentences if s.is_hook)
    peak_count = sum(1 for s in sentences if s.is_peak)
    if curve:
        rhythm = (
            f"{len(segments)} 段 · 情绪形状 {_clean_text(curve.shape)} · "
            f"峰值 {curve.peak_count} 个 / 谷值 {curve.valley_count} 个"
        )
        pacing = (
            f"基准强度 {round(float(curve.baseline_intensity or 0), 2)}，"
            f"强度区间 {round(float(curve.series_min or 0), 2)}~{round(float(curve.series_max or 0), 2)}；"
            f"峰值落点位于全片 {round(float(curve.peak_position_ratio) * 100)}% 处"
        )
    else:
        rhythm = f"{len(segments)} 段（情绪曲线缺失）"
        pacing = "情绪曲线未落库，无法给出节奏诊断"
    content = {
        "rhythm": rhythm,
        "emotion_curve": points,
        "pacing_notes": pacing,
        "retention_hypothesis": [
            f"{_clean_text(t.turn_type)}@{t.t_ms}ms：{_clean_text(t.note)}" for t in turns[:6]
        ]
        or [f"钩子句 {hook_count} 处、峰值句 {peak_count} 处"],
        "platform_notes": f"来源平台 {_clean_text(script.platform or '未知')}",
    }
    return _layer("main", 2, "编导", content, None, model)


def _layer3(segments, model) -> dict:
    items = [
        {
            "seq": s.seq,
            "type": _clean_text(s.seg_type),
            "title": _clean_text(s.title),
            "start_ms": s.start_ms,
            "end_ms": s.end_ms,
            "hook_point": s.hook_point,
            "payoff_point": s.payoff_point,
            "emotion_level": s.emotion_peak,
            "summary": _clean_text(s.summary),
            "purpose": _clean_text(s.purpose),
        }
        for s in segments
    ]
    return {
        "layer": 3,
        "step": "main",
        "role_view": "编导",
        "content": {"segments": items, "segment_count": len(items)},
        "source_type": "ai",
        "confidence": None,
        "model": model,
        "prompt_version": None,
        "raw_response": None,
        "segments": items,
    }


def _layer4(sentences, turns, model) -> dict:
    notes = [
        {
            "note_type": _clean_text(s.sentence_function),
            "start_ms": s.start_ms,
            "end_ms": s.end_ms,
            "role_view": "编导",
            "content": f"{_clean_text(s.quote)}｜诊断：{_clean_text(s.function_reason)}"
            + (f"｜情绪强度 {round(float(s.emotion_intensity), 1)}" if s.emotion_intensity is not None else ""),
            "confidence": s.confidence,
        }
        for s in sentences
        if s.sentence_function in ("冲突", "转折", "高潮", "钩子", "CTA")
    ][:60]
    notes += [
        {
            "note_type": f"情绪{_clean_text(t.turn_type)}",
            "start_ms": t.t_ms,
            "end_ms": t.t_ms,
            "role_view": "编导",
            "content": _clean_text(t.note),
            "confidence": None,
        }
        for t in turns[:20]
    ]
    return {
        "layer": 4,
        "step": "main",
        "role_view": "编导",
        "content": {"note_count": len(notes)},
        "source_type": "ai",
        "confidence": None,
        "model": model,
        "prompt_version": None,
        "raw_response": None,
        "notes": notes,
    }


def _layer5(elements, model) -> dict:
    return {
        "layer": 5,
        "step": "main",
        "role_view": "编导",
        "content": {"element_count": len(elements)},
        "source_type": "ai",
        "confidence": None,
        "model": model,
        "prompt_version": None,
        "raw_response": None,
        "elements": elements,
    }


def _layer(step: str, layer: int, role_view: str, content: dict, score, model) -> dict:
    return {
        "layer": layer,
        "step": step,
        "role_view": role_view,
        "content": content,
        "source_type": "ai",
        "confidence": round(float(score) / 100, 4) if score is not None else None,
        "model": model,
        "prompt_version": None,
        "raw_response": None,
    }


async def _element_cards(db, script, segments, sentences) -> list[dict]:
    """L5 元素卡片：三层积木库（ref_element_source 溯源）→ 旧 elements 卡片结构。"""
    from app.services import element_library

    rows = (
        (
            await db.execute(
                select(M.RefElementSource).where(
                    M.RefElementSource.source_script_id == script.id
                )
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return []
    seg_seq = {s.id: s.seq for s in segments}
    registry = element_library.models()
    loaded: dict[tuple[str, Any], Any] = {}
    by_table: dict[str, set] = {}
    for r in rows:
        by_table.setdefault(r.element_table, set()).add(r.element_id)
    for table, ids in by_table.items():
        model = registry.get(table)
        if model is None:
            continue
        found = (
            (await db.execute(select(model).where(model.id.in_(list(ids)))))
            .scalars()
            .all()
        )
        for row in found:
            loaded[(table, row.id)] = row

    cards: list[dict] = []
    seen: set[tuple[str, Any]] = set()
    for r in rows:
        key = (r.element_table, r.element_id)
        if key in seen:
            continue
        row = loaded.get(key)
        if row is None:
            continue
        seen.add(key)
        evidence = []
        if r.source_segment_id and r.source_segment_id in seg_seq:
            evidence.append({"seg": seg_seq[r.source_segment_id]})
        if r.quote:
            evidence.append({"quote": _clean_text(r.quote)[:120]})
        cards.append(
            {
                "id": element_library.element_id(key[0], row.id),
                "category": element_library._row_category(key[0], row),
                "name": element_library._row_name(key[0], row)[:100],
                "description": element_library._row_description(key[0], row) or None,
                "formula": element_library._row_formula(key[0], row) or None,
                "confidence": element_library._row_confidence(row),
                "role_view": getattr(row, "role_view", None) or "编导",
                "evidence": evidence,
                "status": element_library._row_status(row),
            }
        )
    return cards


# ------------------------------------------------------------------ 视频建档

async def create_video_record(
    db: AsyncSession, payload: dict
) -> tuple[M.Video, bool]:
    """建档；已存在同平台+平台id 则返回既有记录。"""
    stmt = select(M.Video)
    if payload.get("platform") and payload.get("platform_video_id"):
        stmt = stmt.where(
            M.Video.platform == payload["platform"],
            M.Video.platform_video_id == payload["platform_video_id"],
        )
    else:
        stmt = stmt.where(M.Video.url == payload.get("url", ""))
    existing = (await db.execute(stmt.limit(1))).scalar_one_or_none()
    if existing:
        return existing, True

    video = M.Video(
        platform=payload.get("platform") or "unknown",
        platform_video_id=payload.get("platform_video_id"),
        url=payload.get("url") or "",
        title=payload.get("title") or "未命名视频",
        author_name=payload.get("author_name"),
        author_id=payload.get("author_id"),
        cover_url=payload.get("cover_url"),
        duration_ms=payload.get("duration_ms"),
        publish_time=payload.get("publish_time"),
        tags=payload.get("tags") or [],
        stats_snapshot=payload.get("stats_snapshot") or {},
        subtitle_source=payload.get("subtitle_source") or "手动粘贴",
        raw_files={},
        category_guess=payload.get("category_guess"),
    )
    db.add(video)
    await db.flush()
    return video, False


def build_media_brief(manifest: dict) -> str:
    """把多模态采集产物（画面逐段观察/声学特征/BGM）拼成给拆解模型的简报文本。"""
    if not manifest:
        return ""
    parts: list[str] = []

    frames = manifest.get("frames") or []
    if frames:
        shot_lines = []
        for f in frames:
            desc = f.get("desc")
            if not desc:
                continue
            line = f"[{f.get('start_ms', 0)}-{f.get('end_ms', 0)}ms] {desc}"
            if f.get("text_overlay"):
                line += f"｜画面文字：{f['text_overlay']}"
            if f.get("emotion"):
                line += f"｜情绪：{f['emotion']}"
            if f.get("style"):
                line += f"｜风格：{f['style']}"
            shot_lines.append(line)
        if shot_lines:
            parts.append(
                "【画面逐段观察（由视觉模型对逐帧抽帧产出，仅描述客观画面）】\n"
                + "\n".join(shot_lines)
            )

    # 文案来源兜底：ASR 缺失/稀疏（纯字幕卡点、MV、外语片）时，把画面文字(OCR)按时间
    # 顺序补进素材，避免模型"没文案可拆"而产出空泛结论
    try:
        from app.media.pipeline import build_text_source

        src = manifest.get("text_source") or build_text_source(manifest)
        if src.get("mode") in ("ocr_only", "asr+ocr", "none") and src.get("ocr_rows"):
            rows = src["ocr_rows"]
            lines = [f"[{int(r['time_ms'])}ms] {r['text']}" for r in rows]
            parts.append(
                "【画面文字（OCR 提取，按时间顺序，作为文案来源的补充；可能存在识别误差）】\n"
                + "\n".join(lines)
            )
    except Exception:  # noqa: BLE001
        logger.debug("OCR 文案补充失败", exc_info=True)

    audio = manifest.get("audio") or {}
    feats = []
    if audio.get("bpm"):
        feats.append(f"节奏BPM≈{audio['bpm']}")
    if audio.get("mean_volume_db") is not None:
        feats.append(f"平均响度{audio['mean_volume_db']}dB")
    bgm = manifest.get("bgm") or {}
    if bgm.get("ok"):
        feats.append("背景音乐已做人声/伴奏分离")
        if bgm.get("vocal_ratio") is not None:
            feats.append(f"人声/伴奏能量比≈{bgm['vocal_ratio']}")
    if feats:
        parts.append("【声音与背景音乐特征（客观声学指标，请勿臆造曲风与歌名）】\n" + "、".join(feats))

    return "\n\n".join(parts)


async def script_row_counts(db: AsyncSession, video_id) -> dict:
    """三层分库该视频的行数快照（自测/详情页展示用）。"""
    script = await _script_of(db, video_id)
    if script is None:
        return {"script": 0, "segment": 0, "sentence": 0}
    seg = (
        await db.execute(
            select(func.count()).select_from(M.ScriptSegment).where(
                M.ScriptSegment.script_id == script.id
            )
        )
    ).scalar()
    sent = (
        await db.execute(
            select(func.count()).select_from(M.ScriptSentence).where(
                M.ScriptSentence.script_id == script.id
            )
        )
    ).scalar()
    return {"script": 1, "segment": int(seg or 0), "sentence": int(sent or 0)}
