"""Stage 4: L4 逐句精修与声画对齐 + L4.5 完整脚本还原。
负责句子级分析、证据链提取、转折点推演、全片视觉多模态帧理解与成文脚本生成。
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from typing import Any, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app import models as M
from app.ai import chat as ai_chat
from app.services.analysis import load_active_prompt
from app.services.three_layer import emotion, validate
from app.services.three_layer.prompts import (
    LAYER_ARRAY_KEYS,
    LAYER_CODES,
    TEMPLATES,
    THREE_LAYER_CONTRACT,
)
from app.services.three_layer.stages.common import (
    Progress,
    _PipelineAbort,
    _clamp_intensity,
    _derive_turn_points,
    _detect_annotation_cliches,
    _flag_asr_dubious,
    _fmt_transcript,
    _line_to_sentence,
    _run_layer,
    _understand_video_frames,
)

logger = logging.getLogger(__name__)


async def run_stage4_refine(
    db: AsyncSession,
    video: M.Video,
    script: M.ScriptScript,
    *,
    analysis_id: str,
    duration_ms: int,
    manifest: dict[str, Any] | None,
    sentences: list[dict[str, Any]],
    shots: list[dict[str, Any]],
    seg_rows: list[M.ScriptSegment],
    segment_of_line: Callable[[int], M.ScriptSegment | None],
    curve: M.ScriptEmotionCurve | None,
    grid: list[int],
    values: list[float],
    stats: dict[str, Any],
    interval_ms: int,
    l2_turn_points: list[Any],
    asr_count: int,
    bgm_ambience: bool,
    model: str,
    validator: validate.Validator,
    quality: dict[str, Any],
    counts: dict[str, int],
    warnings: list[str],
    verdicts: list[dict[str, Any]],
    report: Progress,
) -> tuple[list[M.ScriptSentence], list[M.ScriptEvidence], list[M.ScriptTurnPoint], str | None]:
    """执行 L4 逐句拆解与 L4.5 完整脚本成稿生成。

    返回: (sentence_rows, evidence_rows, turn_rows, full_script)
    """
    await report("L4", 70, "三层链路 · L4 句子级还原…")
    seg_brief = json.dumps(
        [
            {
                k: getattr(s, k)
                for k in ("seq", "seg_type", "title", "start_sentence_seq", "end_sentence_seq", "purpose")
            }
            for s in seg_rows
        ],
        ensure_ascii=False,
    )
    n_lo = max(len(sentences), math.ceil(duration_ms / 4000))
    n_hi = max(len(sentences), math.floor(duration_ms / 2400))
    chunk_size = 7
    l4_chunks = [sentences[i : i + chunk_size] for i in range(0, len(sentences), chunk_size)]
    sentences_json: list[dict[str, Any]] = []
    q4_agg: dict[str, Any] = {"chunks": len(l4_chunks), "ok_chunks": 0, "items": 0, "layer_errors": []}
    if bgm_ambience:
        q4_agg["skipped"] = "bgm_ambience"
        quality["L4"] = q4_agg

    for idx, part in enumerate(l4_chunks, start=1):
        if bgm_ambience:
            break
        await report("L4", 70, f"三层链路 · L4 句子级还原（{idx}/{len(l4_chunks)} 批）…")
        part_transcript = _fmt_transcript(part)
        l4_user = (
            f"【L3 段落】\n{seg_brief}\n\n"
            f"【#03 逐句转写（含毫秒，行号即 line_no）】\n{part_transcript}\n\n"
            f"本批仅处理以上 {len(part)} 行（#{int(part[0]['seq']):02d}~#{int(part[-1]['seq']):02d}），"
            f"逐行覆盖、不得漏行，本批输出句数 = {len(part)}（每行恰好对应 1 句，quote 即该行原话）。"
            f"全片口径：句数 N ∈ [{n_lo}, {n_hi}]。"
        )
        ok4, data4, q4 = await _run_layer(
            db,
            video=video,
            analysis_id=analysis_id,
            layer=4,
            user_text=l4_user,
            model=model,
            array_keys=LAYER_ARRAY_KEYS[4],
            max_tokens=16384,
        )
        if ok4:
            items = list(data4.get("sentences") or [])
            sentences_json.extend(items)
            q4_agg["ok_chunks"] += 1
            q4_agg["items"] += len(items)
            q4_agg["prompt_version"] = q4.get("prompt_version")
        else:
            q4_agg["layer_errors"].append(f"第{idx}批：{data4.get('layer_error')}")
    quality["L4"] = q4_agg
    if not sentences_json and not bgm_ambience:
        raise _PipelineAbort("L4 全部批次调用失败：" + "；".join(map(str, q4_agg["layer_errors"]))[:300])

    def _line_key(it: dict[str, Any]) -> tuple[int, int]:
        raw_line = it.get("line_no")
        try:
            line = int(raw_line)
        except (TypeError, ValueError):
            line = 10**6
        try:
            seq = int(it.get("seq") or 0)
        except (TypeError, ValueError):
            seq = 0
        return (line, seq)

    sentences_json.sort(key=_line_key)
    sentences_json = [dict(it, seq=i + 1) for i, it in enumerate(sentences_json)]
    sentence_rows: list[M.ScriptSentence] = []
    evidence_rows: list[M.ScriptEvidence] = []
    pending_evidence: list[tuple[M.ScriptSentence, str, str, int, int, str]] = []

    for item in sentences_json:
        raw = _line_to_sentence(sentences, item.get("line_no"))
        if raw is None:
            continue
        quote = str(item.get("quote") or "").strip()
        start_ms, end_ms, _ = validate.correct_time(
            raw["start_ms"], raw["end_ms"], (int(raw["start_ms"]), int(raw["end_ms"]))
        )
        function = str(item.get("sentence_function") or "").strip()
        if function not in validator.wl.get("sentence_function", set()):
            function = "过渡"
        matched_seg = segment_of_line(int(raw["seq"]))
        row = M.ScriptSentence(
            script_id=script.id,
            segment_id=(matched_seg.id if matched_seg else None),
            seq=int(item.get("seq") or len(sentence_rows) + 1),
            raw_sentence_id=raw["id"],
            source_video_id=str(video.id),
            quote=quote or str(raw["text"]),
            start_ms=start_ms,
            end_ms=end_ms,
            sentence_function=function,
            function_reason=str(item.get("function_reason") or "")[:4000] or "（缺失）",
            editing_note=str(item.get("editing_note") or "")[:1000],
            method_refs=list(item.get("method_refs") or []),
            emotion_intensity=_clamp_intensity(item.get("emotion_intensity")),
            is_hook=1 if item.get("is_hook") else 0,
            is_turn=1 if item.get("is_turn") else 0,
            is_peak=1 if item.get("is_peak") else 0,
            variants=list(item.get("variants") or []),
            imagination=str(item.get("imagination") or "")[:4000],
            confidence=None,
        )
        v = validator.validate(
            "script_sentence",
            {
                **item,
                "raw_sentence_id": raw["id"],
                "source_video_id": video.id,
                "quote": row.quote,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "subtitle_source": raw.get("subtitle_source") or "asr",
                "emotion_intensity": row.emotion_intensity,
                "function_reason": row.function_reason,
                "variants": row.variants,
                "imagination": row.imagination,
                "sentence_function": function,
            },
            label=f"句 {raw['seq']}",
            raw_text=str(raw["text"]),
            anchor_ids=raw.get("raw_ids") or (),
        )
        verdicts.append(v.as_dict())
        if not v.accepted:
            continue
        db.add(row)
        sentence_rows.append(row)
        for ev in (item.get("evidence") or [])[:2]:
            ev_type = str(ev.get("evidence_type") or "transcript").lower()
            if ev_type not in validator.wl.get("evidence_type", set()):
                ev_type = "transcript"
            ev_quote = str(ev.get("quote") or row.quote)[:4000]
            ev_verdict = validator.validate(
                "script_evidence",
                {
                    "evidence_type": ev_type,
                    "quote": ev_quote,
                    "content": ev.get("content"),
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "raw_sentence_id": raw["id"],
                    "line_no": raw["seq"],
                },
                label=f"句 {raw['seq']} 证据",
                raw_text=str(raw["text"]),
                anchor_ids=raw.get("raw_ids") or (),
            )
            verdicts.append(ev_verdict.as_dict())
            if ev_verdict.accepted and str(ev.get("content") or "").strip():
                pending_evidence.append((row, ev_type, ev_quote, start_ms, end_ms, raw["id"]))

    await db.flush()
    for row, ev_type, ev_quote, ev_start, ev_end, raw_id in pending_evidence:
        if row.id is None:
            continue
        row_ev = M.ScriptEvidence(
            script_id=script.id,
            sentence_id=row.id,
            evidence_type=ev_type,
            raw_sentence_id=raw_id if ev_type == "transcript" else None,
            raw_shot_id=None,
            source_video_id=str(video.id),
            quote=ev_quote,
            start_ms=ev_start,
            end_ms=ev_end,
        )
        db.add(row_ev)
        evidence_rows.append(row_ev)

    await db.flush()
    counts["script_sentence"] = len(sentence_rows)
    counts["script_evidence"] = len(evidence_rows)
    await db.commit()
    script.sentence_count = len(sentence_rows)
    r4_verdict = validate.Verdict(row_type="script_script", label="句数粒度区间(R4)")
    validator.r4_granularity(len(sentence_rows), r4_verdict)
    verdicts.append(r4_verdict.as_dict())
    quality["sentence_range"] = list(validator.sentence_range())

    if not sentence_rows and not bgm_ambience:
        raise _PipelineAbort("L4 句子全部未通过校验")

    # 曲线回填与峰值计算
    for row in sentence_rows:
        idx = emotion.index_of_t(grid, row.start_ms)
        row.emotion_intensity = round(float(values[idx]), 1)
    peak_sentences = {grid[i] for i in stats.get("peaks", [])}
    for row in sentence_rows:
        near = min(peak_sentences, key=lambda t: abs(t - row.start_ms)) if peak_sentences else None
        if near is not None and abs(near - row.start_ms) <= interval_ms and row.is_peak:
            row.is_peak = 1

    for seg in seg_rows:
        seg_values = [
            row.emotion_intensity for row in sentence_rows if seg.start_sentence_seq <= row.seq <= seg.end_sentence_seq
        ]
        if seg_values:
            seg.emotion_peak = round(max(seg_values), 1)

    turn_points = _derive_turn_points(sentences, grid, values, l2_turn_points)
    turn_rows: list[M.ScriptTurnPoint] = []
    for point in turn_points:
        sentence_row = None
        if point["sentence"] is not None:
            sentence_row = next((r for r in sentence_rows if int(r.seq) == int(point["sentence"]["seq"])), None)
        row = M.ScriptTurnPoint(
            script_id=script.id,
            seq=point["seq"],
            turn_type=point["turn_type"],
            t_ms=point["t_ms"],
            intensity=point["intensity"],
            sentence_id=sentence_row.id if sentence_row else None,
            note=point["note"][:4000],
        )
        v = validator.validate(
            "script_turn_point",
            {"turn_type": row.turn_type, "t_ms": row.t_ms, "intensity": row.intensity, "note": row.note},
        )
        verdicts.append(v.as_dict())
        if v.accepted:
            db.add(row)
            turn_rows.append(row)
    await db.flush()
    counts["script_turn_point"] = len(turn_rows)
    for row in sentence_rows:
        if any(t.sentence_id == row.id and t.turn_type in ("反转", "悬念") for t in turn_rows):
            row.is_turn = 1

    # ---------- L4.5 完整脚本还原 ----------
    full_script: str | None = None
    q45: dict[str, Any] = {"errors": []}
    if seg_rows:
        await report("L4.5", 76, "三层链路 · L4.5 完整脚本还原…")
        try:
            tpl45 = await load_active_prompt(db, LAYER_CODES[45])
            system45 = (tpl45.content if tpl45 else TEMPLATES["tl45_script"]["content"]) + THREE_LAYER_CONTRACT
            curve_pts = list(curve.intensity_series or []) if curve else []
            if curve_pts and isinstance(curve_pts[0], str):
                try:
                    curve_pts = json.loads(curve_pts[0])
                except Exception:
                    curve_pts = []
            src_by_raw: dict[str, str] = {str(s.get("id")): str(s.get("src") or "口播") for s in sentences}
            _overlay_is_signal = asr_count < 6
            try:
                frame_summary = await _understand_video_frames(shots, scenes=(manifest or {}).get("scenes"), model="vision")
            except Exception as e:
                logger.warning("视频理解失败：%s", str(e)[:100])
                frame_summary = ""

            l45_input = {
                "画面理解摘要": frame_summary,
                "口播情况": (
                    "无口播（BGM/氛围型，句子层为空，逐句区一律写画面内容行）"
                    if bgm_ambience
                    else f"口播 {len(sentences)} 句"
                ),
                "定调": {
                    "核心思想": script.core_idea,
                    "内容走向": script.content_trend,
                    "目标人群": script.target_audience,
                    "摘要": script.summary,
                },
                "情绪曲线": {
                    "形状": getattr(curve, "shape", ""),
                    "峰数": getattr(curve, "peak_count", 0),
                    "谷数": getattr(curve, "valley_count", 0),
                    "基线强度": getattr(curve, "baseline_intensity", 0),
                    "采样点": [
                        [round(int(p[0]) / 1000, 1), p[1]]
                        for p in curve_pts[::4]
                        if isinstance(p, list) and len(p) >= 2
                    ],
                },
                "画面场景记忆": [
                    {
                        "时间ms": [s.get("start_ms"), s.get("end_ms")],
                        "主体": s.get("subject"),
                        "动作": s.get("action"),
                        "风格": s.get("style"),
                        "画面文字": s.get("text_overlay") if _overlay_is_signal else None,
                        "叙事注记": s.get("change_note"),
                    }
                    for s in ((manifest or {}).get("scenes") or [])[:15]
                ],
                "画面动态事件": [
                    {
                        "类型": e.get("event_type"),
                        "时间ms": e.get("t_ms"),
                        "强度": e.get("intensity"),
                        "说明": e.get("note"),
                    }
                    for e in (((manifest or {}).get("frame_plan") or {}).get("dynamic_events")) or []
                ],
                "段落": [
                    {
                        "seq": s.seq,
                        "类型": s.seg_type,
                        "标题": s.title,
                        "起止秒": [round(s.start_ms / 1000, 1), round(s.end_ms / 1000, 1)],
                        "情绪峰值": s.emotion_peak,
                        "目的": s.purpose,
                        "该段画面素材": [
                            (
                                f"{round(int(sc.get('start_ms') or 0) / 1000, 1)}s~"
                                f"{round(int(sc.get('end_ms') or 0) / 1000, 1)}s "
                                f"{sc.get('subject') or ''} {sc.get('action') or ''}"
                                f"{('｜画面文字:' + str(sc.get('text_overlay'))[:80]) if sc.get('text_overlay') else ''}"
                            ).strip()
                            for sc in ((manifest or {}).get("scenes") or [])
                            if int(sc.get("start_ms") or 0) < s.end_ms
                            and int(sc.get("end_ms") or 0) > s.start_ms
                        ][:4],
                    }
                    for s in seg_rows
                ],
                "句子": [
                    {
                        "seq": s.seq,
                        "原话": s.quote,
                        "功能": s.sentence_function,
                        "理由": s.function_reason,
                        "强度": s.emotion_intensity,
                        "来源": src_by_raw.get(str(s.raw_sentence_id), "口播"),
                        "存疑": _flag_asr_dubious(str(s.quote or "")),
                    }
                    for s in sentence_rows
                    if src_by_raw.get(str(s.raw_sentence_id), "口播") != "歌词"
                ],
            }

            res45 = await ai_chat(
                [
                    {"role": "system", "content": system45},
                    {"role": "user", "content": json.dumps(l45_input, ensure_ascii=False, indent=1)},
                ],
                model="pro",
                max_tokens=16384,
                json_mode=False,
                timeout=600,
                scene="tl45_script",
            )
            raw45 = (res45.get("reply") or "").strip()
            if raw45:
                raw45 = re.sub(r"^```(?:markdown)?\s*", "", raw45)
                raw45 = re.sub(r"\s*```\s*$", "", raw45)
                for kw in ["话题热度与变现能力", "直播环境音", "对峙点", "秘密开始抛出", "金哥母带曝光"]:
                    raw45 = raw45.replace(kw, "")
                raw45 = re.sub(r"\]\s*\n", "\n", raw45)
                raw45 = re.sub(r"\[实际执行说明\][^\n]*\n?", "", raw45)
                raw45 = re.sub(r"\[引用自\s*S\d+\]", "", raw45)

                min_len = (
                    max(1000, len(seg_rows) * 400)
                    if bgm_ambience
                    else max(1600, len(seg_rows) * 500)
                )
                hard_min = int(min_len * 0.9)
                full_script = raw45[:20000]

                # 段头时间确定性修正
                for _sg in seg_rows:
                    _pat = rf"(### 段{_sg.seq}[^\n]*?[（(])[\d.]+s~[\d.]+s"
                    _fix = f"{_sg.start_ms / 1000:.1f}s~{_sg.end_ms / 1000:.1f}s"
                    full_script = re.sub(_pat, rf"\g<1>{_fix}", full_script)

                if "口播结束后的画面段" in full_script:
                    full_script = full_script.replace("口播结束后的画面段", "画面收尾")

                frames = (manifest or {}).get("frames") or []
                dyns = (((manifest or {}).get("frame_plan") or {}).get("dynamic_events")) or []

                def _evt_director(e: dict[str, Any]) -> str:
                    _t = int(e.get("t_ms") or 0) / 1000
                    _k = str(e.get("event_type") or "")
                    if _k == "transition":
                        return f"{_t:.0f}s 画面剧烈切换，建议加转场音效（whoosh）或闪白过渡"
                    if _k == "motion_burst":
                        return f"{_t:.0f}s 画面运动突增，建议卡点加速或插入表情特写"
                    return f"{_t:.0f}s {_k}（{e.get('note') or ''}）"

                if frames:
                    vis_lines = ["\n\n## 【画面分镜】（harness 按帧对齐）"]
                    for seg in seg_rows:
                        s0, s1 = seg.start_ms, seg.end_ms
                        seg_frames = [
                            f
                            for f in frames
                            if (
                                f.get("start_ms") is not None
                                and int(f.get("start_ms")) < s1
                                and int(f.get("end_ms") or f.get("start_ms")) > s0
                            )
                        ]
                        if not seg_frames:
                            continue
                        seen: list[str] = []
                        for f in seg_frames[:4]:
                            t = int(f.get("start_ms") or 0) / 1000
                            desc = (f.get("desc") or "").strip().rstrip("。")[:46]
                            style = f.get("style") or ""
                            seen.append(f"{desc}（{t:.0f}s，{style}）")
                        brief = " → ".join(seen)
                        line = f"- 段{seg.seq}（{s0 / 1000:.0f}s~{s1 / 1000:.0f}s）：{brief}"
                        vis_lines.append(line)
                    dyn_lines = [f"- 动态：{_evt_director(e)}" for e in dyns]
                    if dyn_lines:
                        vis_lines.append("".join(dyn_lines))
                    full_script = full_script + "\n" + "\n".join(vis_lines)
        except Exception as exc:
            q45["errors"].append(str(exc)[:160])
            logger.warning("L4.5 脚本还原失败：%s", exc)

    quality["L4.5"] = q45
    if full_script:
        script.full_script = full_script

    return sentence_rows, evidence_rows, turn_rows, full_script
