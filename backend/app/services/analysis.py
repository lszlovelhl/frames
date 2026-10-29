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
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, text
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
    "rhythm",
    "completion_drivers",
    "dimensions",
    "quality_gate",
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
    job.failed_reason = message or "unknown_error"
    job.finished_at = _now()


# ------------------------------------------------------------------ 三层拆解

async def run_three_layer_breakdown(
    db: AsyncSession,
    video: M.Video,
    job: M.BreakdownJob,
    *,
    manifest: dict | None = None,
    model: str = "pro",
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

    # 缓存标量：pipeline 异常后会话状态不确定，except 里不得访问 ORM 属性（会触发
    # 过期 lazy load → MissingGreenlet 二次异常，曾导致 job 永久卡 running）
    _vid = video.id
    _job_id = job.id
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
        logger.warning(
            "三层分库拆解失败：video=%s job=%s", _vid, str(_job_id)[:8],
            exc_info=True,
        )
        try:
            await db.rollback()
            db.expunge_all()
        except Exception:  # noqa: BLE001
            pass
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
            err_text = result.get("error") or result.get("message") or "三层拆解失败"
            job.failed_reason = str(err_text)[:1000]
        await db.commit()
    except Exception:  # noqa: BLE001
        logger.warning("三层拆解证据写入 breakdown_jobs 失败", exc_info=True)
        await db.rollback()
        # 兜底：独立 SQL 直标 failed，杜绝 job 永久卡 running（前端轮询会一直挂着）
        try:
            async with db.bind.connect() as conn:
                await conn.execute(
                    text(
                        "UPDATE breakdown_jobs SET status='failed', stage='failed', pct=100, "
                        "failed_reason=:r, updated_at=CURRENT_TIMESTAMP WHERE id=:i"
                    ),
                    {"r": "拆解证据写入失败（详见日志）", "i": str(job.id)},
                )
                await conn.commit()
        except Exception:  # noqa: BLE001
            logger.exception("兜底标记 job failed 失败：%s", job.id)
    return result


# ------------------------------------------------------------------ 两层简化 pipeline

async def run_two_layer_breakdown(
    db: AsyncSession,
    video: M.Video,
    job: M.BreakdownJob,
    *,
    manifest: dict | None = None,
    model: str = "local",
) -> dict[str, Any]:
    """两层简化 pipeline：L1 出框架 → Harness → L2 出成稿。

    与旧三层 pipeline 的区别：
    - 旧：七层模型调用（L1→L2→L3→L4→L45→L5→L6），出错点多
    - 新：两次模型调用（L1 结构化 JSON + L2 Markdown 成稿）+ 一次 harness 后处理
    """
    from app.services.two_layer.pipeline import run_pipeline
    from sqlalchemy import text as sql_text

    _vid = video.id
    _job_id = job.id

    try:
        await set_progress(db, job, stage="two_l1", message="L1：出整体框架...", pct=30)

        duration_sec = video.duration_ms / 1000 if video.duration_ms else 100
        vid_str = str(_vid)

        # 1. 先在异步 session 里把数据都查出来（避免线程池里查数据库导致锁冲突）
        # 从 raw_files.transcript 读转写数据（不是从 raw_transcript_sentence 表）
        raw_files = video.raw_files or {}
        transcript_data = raw_files.get("transcript", {})
        transcript_segments = transcript_data.get("segments", [])

        subtitle_lines = []
        for seg in transcript_segments:
            start_s = seg.get("start", 0)
            end_s = seg.get("end", 0)
            text = seg.get("text", "") or ""
            if text.strip():
                subtitle_lines.append(f"[{start_s:.1f}s-{end_s:.1f}s] {text}")
        subtitle_text = "\n".join(subtitle_lines)

        # 场景数据：从 raw_files.frames 读画面帧描述（每帧都有画面描述+字幕+情绪）
        frames = raw_files.get("frames", [])
        frame_lines = []
        for f in frames:
            time_s = f.get("time_ms", 0) / 1000
            desc = f.get("desc", "") or ""
            overlay = f.get("text_overlay", "") or ""
            emotion = f.get("emotion", "") or ""
            kind = f.get("kind", "") or ""
            if desc:
                parts = [f"{desc}"]
                if overlay:
                    parts.append(f"字幕：{overlay[:50]}")  # 字幕太长截断
                if emotion:
                    parts.append(f"情绪：{emotion}")
                if kind == "motion":
                    parts.append("【运动镜头】")
                elif kind == "transition":
                    parts.append("【转场】")
                frame_lines.append(f"[{time_s:.0f}s] {' | '.join(parts)}")
        scenes_text = "\n".join(frame_lines[:35])  # 去重后30-35帧全部传给模型，信息不丢

        logger.info(f"两层拆解：{len(subtitle_lines)} 句转写，{len(frame_lines)} 个画面帧，{duration_sec:.0f}s")

        # 2. 直接异步调用两层 pipeline（非阻塞，统一走网关）
        from app.services.two_layer.pipeline import run_pipeline

        pipeline_result = await run_pipeline(
            subtitle_text, scenes_text, int(duration_sec), model=model
        )

        await set_progress(db, job, stage="two_l2", message="L2：出完整成稿...", pct=70)

        # 把结果存到数据库
        import uuid
        script_id = uuid.uuid4().hex

        l1_data = pipeline_result["l1_data"]
        markdown = pipeline_result["markdown"]

        # 从 markdown 提取标题（第一行 # 后面的内容）
        title = video.title or "未命名"
        for line in markdown.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break

        # 1. 创建 script_script 记录（先删旧的，因为 video_id 有 UNIQUE 约束）
        await db.execute(sql_text("DELETE FROM script_script WHERE video_id = :vid"), {"vid": str(video.id)})
        
        script = M.ScriptScript(
            video_id=str(video.id),
            title=title,
            platform=video.platform or "",
            category="教育",
            duration_ms=video.duration_ms or 0,
            core_idea=l1_data.get("core_idea", title),
            content_trend="情感治愈类",
            target_audience="家长、教师",
            summary=markdown[:500] if markdown else "",
            sentence_count=len(subtitle_lines),
            status="active",
            full_script=markdown,
        )
        db.add(script)
        await db.flush()  # 让 SQLAlchemy 生成 id
        script_id = script.id

        # 2. 存 segments
        seg_type_map = {"hook": "钩子", "template": "铺垫", "technique": "干货", "climax": "高潮", "cta": "CTA"}
        for i, seg in enumerate(l1_data.get("segments", []), 1):
            seg_type_raw = seg.get("type", "segment").lower()
            seg_type = seg_type_map.get(seg_type_raw, "干货")
            segment = M.ScriptSegment(
                script_id=script_id,
                seq=i,
                seg_type=seg_type,
                title=seg.get("title", ""),
                start_ms=int(seg.get("start_sec", 0) * 1000),
                end_ms=int(seg.get("end_sec", 0) * 1000),
                start_sentence_seq=1,
                end_sentence_seq=len(subtitle_lines),
                purpose=seg.get("purpose", ""),
                summary=seg.get("summary", ""),
                hook_point=0,
                payoff_point=0,
                emotion_peak=5.0,
            )
            db.add(segment)

        # 3. 存 emotion_curve（整个JSON序列）
        curve_points = l1_data.get("emotion_curve", [])
        if curve_points:
            intensity_series = [p.get("intensity", 5.0) for p in curve_points]
            curve = M.ScriptEmotionCurve(
                script_id=script_id,
                sample_interval_ms=10000,
                duration_ms=video.duration_ms or 100000,
                sample_count=len(curve_points),
                intensity_series=intensity_series,
                series_min=min(intensity_series),
                series_max=max(intensity_series),
                shape="波浪",
                peak_position_ratio=0.7,
                peak_count=1,
                valley_count=1,
                baseline_intensity=5.0,
                method="model",
            )
            db.add(curve)

        # 4. 存 elements 到 lib 表
        for elem in l1_data.get("elements", []):
            elem_type = elem.get("type", "technique")
            elem_id = uuid.uuid4().hex
            elem_name = elem.get("name", "")
            elem_formula = elem.get("formula", elem.get("example", ""))

            if elem_type == "hook":
                lib_item = M.LibHook(
                    code=elem_name,
                    name=elem_name,
                    hook_type="悬念",
                    position="前3秒",
                    sentence_pattern=elem_formula,
                    variables=[],
                    expected_effect=elem.get("mechanism", ""),
                    mechanism=elem.get("mechanism", ""),
                    variants=[],
                    imagination="",
                    status="active",
                    quality_score=7.0,
                    source_video_id=str(video.id),
                )
                db.add(lib_item)
            elif elem_type == "technique":
                lib_item = M.LibMethod(
                    code=elem_name,
                    name=elem_name,
                    category="叙事",
                    controlled_tag="通用",
                    abstraction_level="段落级",
                    mechanism=elem.get("mechanism", ""),
                    usage_steps="",
                    counter_example="",
                    variants=[],
                    imagination="",
                    status="active",
                    quality_score=7.0,
                    source_video_id=str(video.id),
                )
                db.add(lib_item)

        evidence = {
            "pipeline": "two_layer",
            "l1_segments": len(l1_data.get("segments", [])),
            "l1_elements": len(l1_data.get("elements", [])),
            "transcript_lines": len(subtitle_lines),
            "scenes": len(frame_lines),
            "markdown_length": len(markdown),
        }

        await mark_done(db, job, evidence=evidence, script_id=script_id, status="done")
        await db.commit()

        return {"ok": True, "evidence": evidence, "script_id": script_id, "markdown": markdown}

    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "两层拆解失败：video=%s job=%s", _vid, str(_job_id)[:8],
            exc_info=True,
        )
        await db.rollback()
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


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


def _split_storyboard(full_script: str) -> tuple[str, list[dict]]:
    """把 full_script 拆成【文案主体】+【画面分镜】结构化块（harness 帧层产物）。"""
    if not full_script:
        return "", []
    marker = "## 【画面分镜】"
    idx = full_script.find(marker)
    if idx < 0:
        return full_script, []
    body = full_script[:idx].rstrip()
    blocks: list[dict] = []
    cur: dict | None = None
    for line in full_script[idx + len(marker):].splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("- 段"):
            m = re.match(r"- 段(\d+)（([\d.]+)s~([\d.]+)s）[：:](.*)", line)
            if m:
                if cur:
                    blocks.append(cur)
                desc = m.group(4)
                overlay = ""
                om = re.search(r"；字幕：(.+)$", desc)
                if om:
                    overlay = om.group(1)
                    desc = desc[: om.start()]
                cur = {
                    "seg": int(m.group(1)),
                    "start_s": float(m.group(2)),
                    "end_s": float(m.group(3)),
                    "shots": [desc],
                    "overlay": overlay,
                    "dynamics": "",
                }
                continue
            if cur:
                cur["shots"].append(line)
        elif line.startswith("- 动态"):
            if cur:
                cur["dynamics"] = line[len("- 动态：") :].strip()
        else:
            if cur:
                cur["shots"].append(line)
    if cur:
        blocks.append(cur)
    return body, blocks


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
            "meta": {
                "three_layer": evidence,
                "progress": {
                    "stage": job.stage or None,
                    "message": job.message or "",
                    "layer": None,
                    "pct": job.pct or (100 if job.status in SETTLED_STATUSES else 0),
                    "updated_at": job.updated_at.isoformat() if job.updated_at else None,
                },
            },
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
    # 刷新 job 对象，避免 MissingGreenlet 错误
    await db.refresh(job)
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
    # 如果full_script是空的（单pipeline结构化存储），就从segments+sentences自动拼接
    full_script_text = _clean_text(script.full_script or "")
    if not full_script_text and segments:
        full_script_text = _build_full_script_from_segments(segments, sentences)

    # 从raw_files读转写数据
    raw = video.raw_files or {}
    transcript = raw.get("transcript") or {}
    transcript_segments_raw = transcript.get("segments") or []
    transcript_text = transcript.get("text") or ""
    
    # 转换格式
    transcript_segments = [
        {
            "start": float(s.get("start", 0)),
            "end": float(s.get("end", 0)),
            "text": s.get("text", ""),
        }
        for s in transcript_segments_raw
    ]

    return {
        "id": str(job.id),
        "video_id": str(job.video_id),
        "status": job.status,
        "current_layer": len(segments) and len(layers) or 0,
        "ai_confidence": confidence,
        "reviewed_by_user": False,
        "full_script": full_script_text,
        "full_script_body": _split_storyboard(full_script_text)[0],
        "storyboard": _split_storyboard(full_script_text)[1],
        "summary": summary,
        "detail": {
            "transcript_segments": transcript_segments,
            "transcript_text": transcript_text,
        },
        "meta": {
            "three_layer": evidence,
            "progress": {
                "stage": job.stage or None,
                "message": job.message or "",
                "layer": None,
                "pct": job.pct or (100 if job.status in SETTLED_STATUSES else 0),
                "updated_at": job.updated_at.isoformat() if job.updated_at else None,
            },
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


def _build_full_script_from_segments(segments: list[M.ScriptSegment], sentences: list[M.ScriptSentence]) -> str:
    """从结构化的segments+sentences拼接出完整的markdown脚本。"""
    lines = []
    
    # 标题
    if segments:
        lines.append(f"# {segments[0].title or '完整脚本'}")
        lines.append("")
    
    # 按分段组织
    seg_sentence_map: dict[str, list[M.ScriptSentence]] = {}
    for s in sentences:
        sid = s.segment_id or ""
        if sid not in seg_sentence_map:
            seg_sentence_map[sid] = []
        seg_sentence_map[sid].append(s)
    
    for seg in segments:
        # 分段标题
        start_s = int(seg.start_ms / 1000) if seg.start_ms else 0
        end_s = int(seg.end_ms / 1000) if seg.end_ms else 0
        lines.append(f"## 段{seg.seq} · {seg.seg_type}｜{start_s}s~{end_s}s")
        lines.append("")
        
        # 分段内容
        if seg.summary:
            lines.append(f"**内容概要**：{seg.summary}")
            lines.append("")
        
        if seg.purpose:
            lines.append(f"**创作目的**：{seg.purpose}")
            lines.append("")
        
        # 逐句分析
        seg_sents = seg_sentence_map.get(seg.id, [])
        if seg_sents:
            lines.append("### 逐句分析")
            lines.append("")
            for s in seg_sents:
                s_start = int(s.start_ms / 1000) if s.start_ms else 0
                intensity = s.emotion_intensity or 5.0
                lines.append(f"**句{s.seq}（{s_start}s）**：「{s.quote}」")
                lines.append(f"- **功能**：{s.sentence_function} — {s.function_reason}")
                lines.append(f"- **情绪强度**：{intensity}/10")
                lines.append("")
    
    return "\n".join(lines)


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

    三层链路落库口径是 ``[[t_ms, intensity], ...]``（见 three_layer/prompts/contract.py 契约），
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
    # 等间隔采样 → 50 个展示点（保留原始时间锚与所属段落，避免丢时间锚）
    step = max(1, len(pairs) // 50) if pairs else 1
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
    ][:20]
    notes += [
        {
            "note_type": f"情绪{_clean_text(t.turn_type)}",
            "start_ms": t.t_ms,
            "end_ms": t.t_ms,
            "role_view": "编导",
            "content": _clean_text(t.note),
            "confidence": None,
        }
        for t in turns[:10]
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
                "mechanism": getattr(row, "mechanism", None),
                "imagination": getattr(row, "imagination", None),
                "usage_steps": getattr(row, "usage_steps", None),
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
