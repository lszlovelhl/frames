"""三层拆解流水线协调器（Pipeline Coordinator）。

按阶段调度 Stage 1~5 完成全链路拆解：
- Stage 1: L1 本片定调与事实锚定
- Stage 2: L2 连续情绪曲线采样与拟合
- Stage 3: L3 结构拆解与段落切分
- Stage 4: L4 逐句精修、对齐与 L4.5 完整脚本成稿
- Stage 5: L5 爆款元素提炼与 L6 组合模板生成
每个阶段具备清晰的输入输出、事务隔离与异常清理保护。
"""
from __future__ import annotations

import logging
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as M
from app.services.three_layer import validate
from app.services.three_layer.raw import load_raw_context, persist_raw_layer
from app.services.three_layer.stages import (
    Progress,
    _PipelineAbort,
    _abort_cleanup,
    _fmt_transcript,
    _looks_lyric,
    purge_three_layer,
    run_stage1_anchor,
    run_stage2_rhythm,
    run_stage3_structure,
    run_stage4_refine,
    run_stage5_elements,
)

logger = logging.getLogger(__name__)

__all__ = [
    "Progress",
    "_PipelineAbort",
    "_abort_cleanup",
    "purge_three_layer",
    "run_three_layer",
]


def _classify_route(
    *,
    asr_count: int,
    units: list[dict[str, Any]],
    duration_ms: int,
    shots: list[dict[str, Any]],
) -> dict[str, Any]:
    """视频类型路由画像 —— agent 动态编排的决策层。

    开跑前先判断视频画像（口播密度 / 时长 / 画面素材量），据此产出
    ``vtype``（bgm / talking_head / mixed）、``length``（short/medium/long）
    与策略建议（``compact_mode``、``notes``）。当前版本先做观测与记录，
    后续阶段再让该决策真正驱动 stage 资源分配（重试预算 / model 选择）。
    """
    duration_s = (duration_ms or 0) / 1000
    ocr_n = sum(1 for u in units if str(u.get("source") or "") == "ocr")

    if asr_count <= 1 and ocr_n <= 2:
        vtype = "bgm"  # BGM/氛围型：口播几乎为零，OCR 多为水印/标题
    elif asr_count >= 20:
        vtype = "talking_head"  # 高密度口播
    else:
        vtype = "mixed"

    if duration_s < 60:
        length = "short"
    elif duration_s < 300:
        length = "medium"
    else:
        length = "long"

    notes: list[str] = []
    compact_mode = length == "long"
    if vtype == "bgm":
        notes.append("BGM/氛围型：L4 逐句精修跳过，走纯画面场景分支")
    if compact_mode:
        notes.append(f"长视频（{duration_s:.0f}s）：建议收紧 LLM 重试预算，防 token 失控")

    return {
        "vtype": vtype,
        "length": length,
        "duration_s": round(duration_s, 1),
        "asr_count": asr_count,
        "ocr_n": ocr_n,
        "shots_n": len(shots),
        "compact_mode": compact_mode,
        "notes": notes,
    }


async def run_three_layer(
    db: AsyncSession,
    video: M.Video,
    *,
    analysis_id: str,
    manifest: dict[str, Any] | None = None,
    ctx: dict[str, Any] | None = None,
    model: str = "flash",
    progress: Progress | None = None,
) -> dict[str, Any]:
    """执行 L1~L6 并把结果写入三层表，返回落库统计与校验证据。

    事务语义：阶段内分批提交释放锁；发生异常时统一回滚未提交写入并清除残留中间产物。
    """
    counts: dict[str, int] = {}
    verdicts: list[dict[str, Any]] = []
    try:
        return await _run_three_layer_main(
            db,
            video,
            analysis_id=analysis_id,
            manifest=manifest,
            ctx=ctx,
            model=model,
            progress=progress,
        )
    except _PipelineAbort as exc:
        await _abort_cleanup(db, video.id)
        return {"ok": False, "error": exc.message, "counts": counts, "verdicts": verdicts}
    except Exception as exc:  # noqa: BLE001
        await _abort_cleanup(db, video.id)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "counts": counts, "verdicts": verdicts}


async def _run_three_layer_main(
    db: AsyncSession,
    video: M.Video,
    *,
    analysis_id: str,
    manifest: dict[str, Any] | None = None,
    ctx: dict[str, Any] | None = None,
    model: str = "flash",
    progress: Progress | None = None,
) -> dict[str, Any]:
    async def report(stage: str, pct: int, msg: str) -> None:
        if progress is not None:
            try:
                await progress(stage, pct, msg)
            except Exception:  # noqa: BLE001
                logger.debug("三层链路进度回调失败", exc_info=True)

    # 1. 事务清理历史产物
    await purge_three_layer(db, video.id)

    # 2. 保真原料层准备
    if ctx is None:
        if manifest:
            ctx = await persist_raw_layer(db, video, manifest)
        else:
            ctx = await load_raw_context(db, video.id)
            if not ctx["sentences"]:
                ctx = await persist_raw_layer(db, video, {})
    if not ctx.get("sentences"):
        raise _PipelineAbort("保真原料层无可用转写句子（raw_transcript_sentence 为空），无法执行拆解")

    raw_sentences: list[dict[str, Any]] = ctx["sentences"]
    shots: list[dict[str, Any]] = list(ctx.get("shots", []))
    duration_ms: int = int(ctx.get("duration_ms") or video.duration_ms or 0)

    # 3. 短碎片合并与口播分流
    MIN_QUOTE_LEN = 8
    units: list[dict[str, Any]] = []
    buf: list[dict[str, Any]] = []

    def _unit_text(group: Sequence[dict[str, Any]]) -> str:
        return "".join(str(x["text"]).strip() for x in group)

    def _emit(group: list[dict[str, Any]]) -> None:
        units.append({
            "id": group[0]["id"],
            "start_ms": int(group[0]["start_ms"]),
            "end_ms": int(group[-1]["end_ms"]),
            "text": _unit_text(group),
            "raw_ids": [str(x["id"]) for x in group],
            "raw_seqs": [int(x["seq"]) for x in group],
            "source": "ocr" if any(str(x.get("source") or "") == "ocr" for x in group) else "asr",
        })

    for s in raw_sentences:
        buf.append(s)
        if len(_unit_text(buf)) >= MIN_QUOTE_LEN:
            _emit(buf)
            buf = []
    if buf:
        if units:
            tail = buf
            units[-1]["end_ms"] = int(tail[-1]["end_ms"])
            units[-1]["text"] = units[-1]["text"] + _unit_text(tail)
            units[-1]["raw_ids"].extend(str(x["id"]) for x in tail)
            units[-1]["raw_seqs"].extend(int(x["seq"]) for x in tail)
        else:
            _emit(buf)

    for i, u in enumerate(units, start=1):
        u["seq"] = i

    warnings: list[str] = []
    asr_count = sum(1 for u in units if str(u.get("source") or "") != "ocr")
    bgm_ambience = False
    if asr_count < 6:
        _ocr_t = {
            str(u.get("text") or "").strip()
            for u in units
            if str(u.get("source") or "") == "ocr" and str(u.get("text") or "").strip()
        }
        if asr_count <= 1 and len(_ocr_t) <= 2:
            bgm_ambience = True
            warnings.append(
                "BGM/氛围型视频（口播≤1句 + OCR 为覆盖水印/标题）：句子层置空，拆解改走纯画面场景分支"
            )

    if bgm_ambience:
        units = []
    elif asr_count >= 6 and any(str(u.get("source") or "") == "ocr" for u in units):
        ocr_n = sum(1 for u in units if str(u.get("source") or "") == "ocr")
        units = [u for u in units if str(u.get("source") or "") != "ocr"]
        warnings.append(f"口播句充足（{asr_count}句），画面字幕 {ocr_n} 条仅作画面参考，未进句子层")

    for u in units:
        if str(u.get("source") or "") == "ocr":
            u["src"] = "画面字幕"
        else:
            u["src"] = "歌词" if _looks_lyric(str(u["text"])) else "口播"

    sentences: list[dict[str, Any]] = units
    route = _classify_route(
        asr_count=asr_count, units=units, duration_ms=duration_ms, shots=shots
    )
    warnings.extend(route["notes"])
    line_map = {int(s["seq"]): s for s in sentences}
    tags = (await db.execute(select(M.LibTag.code, M.LibTag.dimension))).all()
    raw_by_id = {str(s["id"]): s for s in sentences}
    raw_chain = [{"id": str(s["id"]), "seq": int(s["seq"]), "text": str(s["text"])} for s in raw_sentences]
    raw_frag_by_id = {str(s["id"]): s for s in raw_sentences}

    validator = validate.Validator(
        duration_ms=duration_ms,
        raw_by_seq=line_map,
        raw_by_id=raw_by_id,
        raw_chain=raw_chain,
        shot_descs=[s["desc"] for s in shots],
        whitelists=validate.load_whitelists([(c, d) for c, d in tags]),
    )

    await db.commit()
    verdicts: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    quality: dict[str, Any] = {}

    title = str(video.title or "")
    base_meta = (
        f"标题：{title or '（无）'}\n平台：{ctx['platform']}\n"
        f"片长：{duration_ms}ms（约 {round(duration_ms / 1000, 1)} 秒）"
    )
    transcript = _fmt_transcript(sentences, with_ms=False)
    if bgm_ambience:
        transcript = "（本视频为纯画面/BGM/氛围型，无口播内容，请主要依据画面参考做定调）"

    # 动态编排：根据视频画像调整 L1 输出预算（省 token：非 BGM 也用 4096）
    l1_max_tokens = 4096
    warnings.append(f"L1 输出预算：{l1_max_tokens} tokens")

    visual_ref_lines = []
    for _sh in shots[:4]:  # 省 token：8 帧→4 帧
        _d = str(_sh.get("desc") or "").strip()
        _ov = str(_sh.get("text_overlay") or "").strip()
        if not _d and not _ov:
            continue
        _line = f"场景{_sh.get('seq')}（{int(_sh.get('start_ms') or 0) / 1000:.0f}s）：{_d}"
        if _ov:
            _line += f"｜{_ov[:80]}"
        visual_ref_lines.append(_line)
    visual_ref = "\n".join(visual_ref_lines) or "（无）"

    # ---------- Stage 1: L1 本片定调 ----------
    l1_max_attempts = 2 if route["compact_mode"] else 4
    warnings.append(f"L1 重试预算：{l1_max_attempts} 次（{'长视频紧凑模式' if route['compact_mode'] else '标准'}）")
    script, data1, q1, v1 = await run_stage1_anchor(
        db,
        video,
        analysis_id=analysis_id,
        title=title,
        base_meta=base_meta,
        visual_ref=visual_ref,
        transcript=transcript,
        duration_ms=duration_ms,
        ctx=ctx,
        model=model,
        validator=validator,
        report=report,
        max_attempts=l1_max_attempts,
        max_tokens=l1_max_tokens,
    )
    quality["L1"] = q1
    verdicts.append(v1)
    counts["script_script"] = 1

    # ---------- Stage 2: L2 情绪曲线 ----------
    curve, curve_grid, curve_values, l2_turn_points, stats = await run_stage2_rhythm(
        db,
        video,
        script,
        analysis_id=analysis_id,
        duration_ms=duration_ms,
        data1=data1,
        ctx=ctx,
        sentences=sentences,
        model=model,
        quality=quality,
        counts=counts,
        warnings=warnings,
        report=report,
    )

    # ---------- Stage 3: L3 段落切分 ----------
    seg_rows, segment_of_line = await run_stage3_structure(
        db,
        video,
        script,
        analysis_id=analysis_id,
        duration_ms=duration_ms,
        manifest=manifest,
        data1=data1,
        transcript=transcript,
        sentences=sentences,
        units=units,
        asr_count=asr_count,
        bgm_ambience=bgm_ambience,
        stats=stats,
        curve=curve,
        model=model,
        validator=validator,
        quality=quality,
        counts=counts,
        warnings=warnings,
        verdicts=verdicts,
        report=report,
    )

    # ---------- Stage 4: L4 逐句精修 & L4.5 完整成文 ----------
    sentence_rows, evidence_rows, turn_rows, full_script = await run_stage4_refine(
        db,
        video,
        script,
        analysis_id=analysis_id,
        duration_ms=duration_ms,
        manifest=manifest,
        sentences=sentences,
        shots=shots,
        seg_rows=seg_rows,
        segment_of_line=segment_of_line,
        curve=curve,
        grid=curve_grid,
        values=curve_values,
        stats=stats,
        interval_ms=curve.sample_interval_ms,
        l2_turn_points=l2_turn_points,
        asr_count=asr_count,
        bgm_ambience=bgm_ambience,
        model=model,
        validator=validator,
        quality=quality,
        counts=counts,
        warnings=warnings,
        verdicts=verdicts,
        report=report,
    )

    # ---------- Stage 5: L5 元素提炼 & L6 组合模板 ----------
    lib_counts, combo_row, slot_rows = await run_stage5_elements(
        db,
        video,
        script,
        analysis_id=analysis_id,
        duration_ms=duration_ms,
        ctx=ctx,
        sentences=sentences,
        sentence_rows=sentence_rows,
        seg_rows=seg_rows,
        segment_of_line=segment_of_line,
        curve_grid=curve_grid,
        curve_values=curve_values,
        stats=stats,
        interval_ms=curve.sample_interval_ms,
        model=model,
        validator=validator,
        quality=quality,
        counts=counts,
        warnings=warnings,
        verdicts=verdicts,
        report=report,
    )

    # ---------- 最终收尾与校验证据汇编 ----------
    verdict_list = verdicts
    rejected = [v for v in verdict_list if v.get("status") == "rejected"]
    draft = [v for v in verdict_list if v.get("status") == "draft"]

    def _quote_span_of(text: str) -> dict[str, Any] | None:
        return validator.quote_span(text) if str(text or "").strip() else None

    sent_total = max(1, len(sentence_rows))
    verbatim_rows = [r for r in sentence_rows if _quote_span_of(r.quote)]
    cross_rows = [r for r in verbatim_rows if (_quote_span_of(r.quote) or {}).get("span", 1) > 1]
    single_raw_ok = [
        r
        for r in sentence_rows
        if str(r.quote).strip()
        and validate.similarity(
            validate.normalize_quote(str(r.quote)),
            validate.normalize_quote(str(raw_frag_by_id.get(str(r.raw_sentence_id), {}).get("text") or "")),
        )
        >= 0.9
    ]

    rule_hard: dict[str, int] = {}
    rule_soft: dict[str, int] = {}
    for v in verdict_list:
        for msg in v.get("hard") or []:
            key = str(msg).split(" ")[0]
            rule_hard[key] = rule_hard.get(key, 0) + 1
        for msg in v.get("soft") or []:
            key = str(msg).split(" ")[0]
            rule_soft[key] = rule_soft.get(key, 0) + 1

    evidence = {
        "counts": counts,
        "quality": quality,
        "curve": quality.get("curve"),
        "sentence_range": quality.get("sentence_range"),
        "quote_nonnull_rate": round(
            sum(1 for r in sentence_rows if r.quote.strip()) / max(1, len(sentence_rows)), 4
        ),
        "quoted_from_raw_rate": round(len(verbatim_rows) / sent_total, 4),
        "quote_verbatim_rate": round(len(verbatim_rows) / sent_total, 4),
        "quote_verbatim_detail": {
            "total": len(sentence_rows),
            "verbatim": len(verbatim_rows),
            "cross_sentence": len(cross_rows),
            "cross_sentence_examples": [
                {
                    "seq": int(r.seq),
                    "raw_seqs": (_quote_span_of(r.quote) or {}).get("raw_seqs"),
                    "quote": str(r.quote)[:40],
                }
                for r in cross_rows[:8]
            ],
        },
        "quoted_from_raw_rate_single_raw": round(len(single_raw_ok) / sent_total, 4),
        "rule_stats": {"hard": rule_hard, "soft": rule_soft},
        "validation": {
            "total_rows": len(verdict_list),
            "rejected": len(rejected),
            "draft": len(draft),
            "active": len(verdict_list) - len(rejected) - len(draft),
            "soft_total": sum(len(v.get("soft") or []) for v in verdict_list),
            "rejected_rows": rejected[:20],
            "draft_rows": draft[:20],
        },
        "warnings": warnings,
        "routing": route,
        "lib_counts": lib_counts,
        "turn_point_count": len(turn_rows),
        "element_baseline": validate.element_baseline(verdict_list),
        "verdicts": verdict_list,
    }

    script.summary = script.summary or ""
    script.quality_score = round(
        sum(float(v.get("quality_score") or 100) for v in verdict_list) / max(1, len(verdict_list)), 2
    )
    script.status = "active" if not rejected else "draft"
    await db.flush()

    return {"ok": True, "script_id": script.id, **evidence}
