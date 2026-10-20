"""Stage 3: L3 结构拆解与段落切分。
负责段落功能切分、时间轴分段校验、口播停顿兜底、段落扩写与 ScriptSegment 持久化。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app import models as M
from app.services.llm_json import complete_json
from app.services.three_layer import validate
from app.services.three_layer.prompts import LAYER_ARRAY_KEYS
from app.services.three_layer.stages.common import (
    L3_SEGMENT_MAX,
    L3_SEGMENT_MIN,
    Progress,
    _PipelineAbort,
    _clamp_intensity,
    _line_to_sentence,
    _next_sentence_after,
    _run_layer,
)

logger = logging.getLogger(__name__)


async def run_stage3_structure(
    db: AsyncSession,
    video: M.Video,
    script: M.ScriptScript,
    *,
    analysis_id: str,
    duration_ms: int,
    manifest: dict[str, Any] | None,
    data1: dict[str, Any],
    transcript: str,
    sentences: list[dict[str, Any]],
    units: list[dict[str, Any]],
    asr_count: int,
    bgm_ambience: bool,
    stats: dict[str, Any],
    curve: M.ScriptEmotionCurve | None,
    model: str,
    validator: validate.Validator,
    quality: dict[str, Any],
    counts: dict[str, int],
    warnings: list[str],
    verdicts: list[dict[str, Any]],
    report: Progress,
) -> tuple[list[M.ScriptSegment], Callable[[int], M.ScriptSegment | None]]:
    """执行 L3 段落切分、时间轴单调校验并写入 ScriptSegment。

    返回: (seg_rows, segment_of_line)
    """
    await report("L3", 64, "三层链路 · L3 段落切分…")
    seg_hints: list[int] = []
    dur_ms = int((manifest or {}).get("duration_ms") or 0) or duration_ms
    if asr_count >= 6:
        for a, b in zip(units, units[1:]):
            gap = (b.get("start_ms") or 0) - (a.get("end_ms") or 0)
            if gap > 1500:
                seg_hints.append(int(round(int(b["start_ms"]) / 1000)))
        last_end = max((int(u.get("end_ms") or 0) for u in units), default=0)
        if dur_ms > 0 and last_end < dur_ms * 0.9 and (dur_ms - last_end) >= 5000:
            seg_hints.append(int(round(last_end / 1000)))
    else:
        for sc in ((manifest or {}).get("scenes") or []):
            st = int(sc.get("start_ms") or 0) / 1000
            if st > 1.0:
                seg_hints.append(int(round(st)))
    seg_hints = sorted(set(seg_hints))[:4]
    hint_text = ""
    if seg_hints:
        hint_text = (
            f"\n\n【harness 分段参考】口播停顿与口播结束点"
            f"在以下秒点附近（叙事阶段转换的强信号，非强制）："
            f"{'、'.join(f'{p}s' for p in seg_hints)}。"
            f"这些点附近若确有叙事/话题转换（如口播话题转变、口播结束后转为画面演示），"
            f"应作为段落边界；若口播连续无转折，仍可 1 段。"
        )

    l3_user = (
        f"【L1 结论】\n"
        + json.dumps(
            {
                k: data1.get(k)
                for k in ("core_idea", "content_trend", "target_audience", "hook_type", "narrative_order")
            },
            ensure_ascii=False,
        )
        + f"\n\n【#03 逐句转写（含毫秒，行号即 line_no）】\n{transcript}\n\n片长：{duration_ms}ms"
        + hint_text
    )

    if bgm_ambience:
        _scenes = (manifest or {}).get("scenes") or []
        if not _scenes:
            _scenes = [
                {
                    "start_ms": 0,
                    "end_ms": dur_ms,
                    "subject": str(video.title or "")[:12],
                    "action": "",
                    "change_note": "",
                }
            ]
        segments_json = [
            {
                "seq": i,
                "seg_type": "高潮" if i == len(_scenes) else "铺垫",
                "title": (f"{sc.get('subject') or ''}{sc.get('action') or ''}")[:28],
                "start_ms": int(sc.get("start_ms") or 0),
                "end_ms": int(sc.get("end_ms") or dur_ms),
                "purpose": f"画面场景 {i}/{len(_scenes)}（{sc.get('change_note') or '画面延续'}）",
                "summary": f"画面场景 {i}：{sc.get('subject') or ''}{sc.get('action') or ''}",
                "emotion_level": stats.get("baseline_intensity") or 0,
            }
            for i, sc in enumerate(_scenes, start=1)
        ]
        ok3, data3, q3 = True, {"segments": segments_json}, {
            "items": len(segments_json),
            "skipped": "bgm_ambience",
            "prompt_version": 1,
        }
        quality["L3"] = q3
        await report("L3", 62, f"三层链路 · L3 画面场景分段（BGM/氛围型，{len(segments_json)} 段）…")
    else:
        ok3, data3, q3 = await _run_layer(
            db,
            video=video,
            analysis_id=analysis_id,
            layer=3,
            user_text=l3_user,
            model="flash",
            array_keys=LAYER_ARRAY_KEYS[3],
            max_tokens=16384,
        )
        quality["L3"] = q3
        segments_json = (data3.get("segments") or []) if ok3 else []

    target_seg_n = max(2, min(5, round(duration_ms / 20000)))
    if not bgm_ambience and len(segments_json) < target_seg_n:
        retry_hint = (
            f"\n\n【校验反馈】上次返回 {len(segments_json)} 段，本片 {duration_ms / 1000:.0f}s"
            f"按颗粒度应拆 {target_seg_n}~{L3_SEGMENT_MAX} 个功能段"
            f"（{duration_ms / 1000:.0f}s 视频至少 {target_seg_n} 段）。"
            f"请重新切分：段落必须按行号边界（#nn）切分，段数 {target_seg_n}~{L3_SEGMENT_MAX}，"
            f"覆盖全片、首尾相接、不重叠；**宁可多分真实话题阶段，不要糊成大段**。"
        )
        await report("L3", 66, f"三层链路 · L3 段数不足（{len(segments_json)}<{target_seg_n}），自动重试…")
        ok3b, data3b, q3b = await _run_layer(
            db,
            video=video,
            analysis_id=analysis_id,
            layer=3,
            user_text=l3_user + retry_hint,
            model=model,
            array_keys=LAYER_ARRAY_KEYS[3],
            max_tokens=16384,
        )
        quality["L3_retry"] = {**q3b, "first_count": len(segments_json), "retried": True}
        if ok3b:
            segments_json = data3b.get("segments") or []
            quality["L3"] = q3b
            if len(segments_json) >= target_seg_n:
                await report("L3", 67, f"三层链路 · L3 重试达标（{len(segments_json)} 段）…")

    asr_seg_n = sum(
        1
        for s in segments_json
        if (s.get("line_from") is not None and s.get("line_from") != "")
        or (s.get("line_to") is not None and s.get("line_to") != "")
    )
    if not bgm_ambience and asr_count >= 6 and 0 < asr_seg_n < 2:
        retry_hint2 = (
            f"\n\n【校验反馈】口播句充足（{asr_count} 句），但口播段只有 {asr_seg_n} 个。"
            f"请按叙事阶段把口播内容切成 ≥2 个口播段（如：开场引入→冲突/话题展开→收尾/转折），"
            f"每段必须覆盖真实的口播行号区间（#nn），画面段另算、不占用口播段数。"
        )
        await report("L3", 66, f"三层链路 · L3 口播段过少（{asr_seg_n}<2），细分重试…")
        ok3c, data3c, q3c = await _run_layer(
            db,
            video=video,
            analysis_id=analysis_id,
            layer=3,
            user_text=l3_user + retry_hint2,
            model=model,
            array_keys=LAYER_ARRAY_KEYS[3],
            max_tokens=16384,
        )
        quality["L3_retry2"] = {**q3c, "first_asr_seg_n": asr_seg_n, "retried": True}
        if ok3c:
            segments_json = data3c.get("segments") or []
            quality["L3"] = q3c

    if len(segments_json) > L3_SEGMENT_MAX:
        warnings.append(f"L3 段数 {len(segments_json)} 超过上限 {L3_SEGMENT_MAX}（保留全部段落，未截断）")
    if not segments_json:
        segments_json = [
            {
                "seq": 1,
                "seg_type": "钩子",
                "title": (video.title or "全片")[:12],
                "line_from": 1,
                "line_to": max(1, len(sentences)),
                "purpose": "模型未返回段落，按全片兜底为一个段落",
                "summary": "全片口播",
                "hook_point": True,
                "payoff_point": True,
                "emotion_level": stats.get("baseline_intensity") or 0,
            }
        ]
        warnings.append("L3 未返回可用段落（重试后仍为空），已按全片兜底")

    # --- 模型分段质量检测 + harness 停顿兜底 ---
    _ordered = [
        x
        for x in segments_json
        if (x.get("line_from") is not None and x.get("line_from") != "")
        or (x.get("line_to") is not None and x.get("line_to") != "")
    ]
    _covered_seq: set[int] = set()
    _valid, _cur = 0, 0
    for _x in _ordered:
        try:
            _f = int(str(_x.get("line_from") or "").strip())
        except (TypeError, ValueError):
            _f = None
        if _f is not None and _f > _cur:
            _valid += 1
            _cur = _f
        try:
            _t = int(str(_x.get("line_to") or "").strip())
        except (TypeError, ValueError):
            _t = None
        if _f is not None and _t is not None and _f <= _t:
            _covered_seq.update(range(_f, _t + 1))
    _uncovered = [x for x in sentences if int(x["seq"]) not in _covered_seq]

    def _seg_has_pause(_fr: int, _to: int) -> bool:
        _prev = None
        for _s in sentences:
            if int(_s["seq"]) < _fr or int(_s["seq"]) > _to:
                continue
            if _prev is not None and int(_s["start_ms"]) - _prev >= 1500:
                return True
            _prev = int(_s["end_ms"])
        return False

    _pause_inside = False
    for _x in _ordered:
        try:
            _fr = int(str(_x.get("line_from") or "").strip())
            _to = int(str(_x.get("line_to") or "").strip())
        except (TypeError, ValueError):
            continue
        if _fr and _to and _fr <= _to and _seg_has_pause(_fr, _to):
            _pause_inside = True
            break

    if (
        (len(_ordered) >= 3 and _valid < 2)
        or (len(_uncovered) >= 1 and len(sentences) >= 3)
        or _pause_inside
    ):
        if _uncovered:
            warnings.append(
                f"L3 模型分段漏覆盖口播句 {[int(x['seq']) for x in _uncovered]}，harness 按口播停顿确定性兜底重建"
            )
        elif _pause_inside:
            warnings.append("L3 模型分段颗粒度过粗（某段内部跨越长停顿），harness 按口播停顿确定性兜底重建")
        if len(_ordered) >= 3 and _valid < 2:
            warnings.append(
                f"L3 模型分段质量差（{len(_ordered)} 段仅 {_valid} 段行号有效），harness 按口播停顿确定性兜底重建"
            )
        _segs: list[dict[str, Any]] = []
        _cur_s: dict[str, Any] | None = None
        _prev_end = None
        for _s in sentences:
            if _cur_s is None:
                _cur_s = {"first": _s, "last": _s}
            else:
                _gap = int(_s["start_ms"]) - (_prev_end or 0)
                if _gap >= 1500:
                    _segs.append(_cur_s)
                    _cur_s = {"first": _s, "last": _s}
                else:
                    _cur_s["last"] = _s
            _prev_end = int(_s["end_ms"])
        if _cur_s:
            _segs.append(_cur_s)
        segments_json = [
            {
                "seq": i,
                "seg_type": "干货",
                "title": "",
                "line_from": int(_g["first"]["seq"]),
                "line_to": int(_g["last"]["seq"]),
                "purpose": "",
                "summary": f"口播句 {_g['first']['seq']}~{_g['last']['seq']}",
                "emotion_level": stats.get("baseline_intensity") or 0,
            }
            for i, _g in enumerate(_segs, start=1)
        ]
        warnings.append(f"L3 harness 兜底重建 {len(segments_json)} 个口播段（按停顿）")

    # --- L3b：段落内容扩写 ---
    await report("L3", 65, "三层链路 · L3b 段落内容扩写…")
    for _seg in segments_json:
        try:
            _p = str(_seg.get("purpose") or "")
            _s = str(_seg.get("summary") or "")
            if len(_p) < 80 or len(_s) < 50:
                _f = _seg.get("line_from")
                _t = _seg.get("line_to")
                _lines = []
                if _f and _t:
                    for _sent in sentences:
                        if int(_sent["seq"]) >= int(_f) and int(_sent["seq"]) <= int(_t):
                            _lines.append(f"#{_sent['seq']}: {_sent.get('quote', _sent.get('text', ''))}")
                _lines_txt = "\n".join(_lines) if _lines else "（无口播台词，纯画面段）"

                _expand_prompt = (
                    "你是短视频编导。下面是一段视频的段落切分结果。请严格根据这段台词，写出 purpose 和 summary。\n\n"
                    f"【这段的台词】\n{_lines_txt}\n\n"
                    "铁律：\n"
                    "1. purpose 里必须出现台词中的具体关键词，禁止凭空生成无关场景；\n"
                    "2. purpose ≥80字：写清承担什么具体动作、为什么放在这个位置；\n"
                    "3. summary ≥50字：陈述具体事实与关键台词。\n\n"
                    "请输出 JSON：{\"purpose\": \"...\", \"summary\": \"...\"}"
                )
                _resp = await complete_json(
                    [{"role": "user", "content": _expand_prompt}],
                    array_keys=["purpose", "summary"],
                    model="flash",
                    temperature=0.3,
                    max_tokens=1024,
                    timeout=120,
                    scene="three_layer_l3b",
                    ref_type="analysis",
                    ref_id=str(analysis_id),
                    max_rounds=2,
                )
                if _resp and _resp[0]:
                    _expanded = _resp[0]
                    if _expanded.get("purpose") and len(str(_expanded["purpose"])) > len(_p):
                        _seg["purpose"] = str(_expanded["purpose"])[:4000]
                    if _expanded.get("summary") and len(str(_expanded["summary"])) > len(_s):
                        _seg["summary"] = str(_expanded["summary"])[:4000]
        except Exception as _exc:
            logger.warning("L3b 段落扩写失败（段 %s）：%s", _seg.get("seq"), _exc)

    seg_rows: list[M.ScriptSegment] = []
    _prev_last: dict[str, Any] | None = None
    for item in segments_json:
        has_lines = item.get("line_from") is not None or item.get("line_to") is not None
        if not has_lines:
            st_ms = int(item.get("start_ms") or (seg_rows[-1].end_ms if seg_rows else 0))
            en_ms = int(item.get("end_ms") or dur_ms)
            st_ms = max(st_ms, seg_rows[-1].end_ms if seg_rows else 0)
            en_ms = max(en_ms, st_ms + 500)
            if dur_ms:
                en_ms = min(en_ms, dur_ms)
            prev_seq = seg_rows[-1].end_sentence_seq if seg_rows else 0
            seg_type = str(item.get("seg_type") or "").strip()
            if seg_type not in validator.wl.get("seg_type", set()):
                seg_type = "铺垫"
            _existing_seqs = {s.seq for s in seg_rows}
            _new_seq = int(item.get("seq") or 0)
            while _new_seq in _existing_seqs:
                _new_seq += 1
            row = M.ScriptSegment(
                script_id=script.id,
                seq=_new_seq,
                seg_type=seg_type,
                title=(str(item.get("title") or "")[:128] or None),
                start_ms=st_ms,
                end_ms=en_ms,
                start_sentence_seq=prev_seq,
                end_sentence_seq=prev_seq,
                purpose=str(item.get("purpose") or "")[:4000] or "（缺失）",
                summary=str(item.get("summary") or "")[:4000] or "（缺失）",
                hook_point=1 if item.get("hook_point") else 0,
                payoff_point=1 if item.get("payoff_point") else 0,
                emotion_peak=_clamp_intensity(item.get("emotion_level")),
            )
            v = validator.validate(
                "script_segment",
                {
                    **item,
                    "start_ms": st_ms,
                    "end_ms": en_ms,
                    "seg_type": seg_type,
                    "start_sentence_seq": prev_seq,
                    "end_sentence_seq": prev_seq,
                },
                label=f"seg{row.seq}:{row.title or ''}",
            )
            verdicts.append(v.as_dict())
            if not v.accepted:
                continue
            db.add(row)
            seg_rows.append(row)
            warnings.append(
                f"L3 段{item.get('seq')} 为纯画面段（无口播锚点，{st_ms}ms~{en_ms}ms，时间锚定保留）"
            )
            continue

        s_first = _line_to_sentence(sentences, item.get("line_from"))
        s_last = _line_to_sentence(sentences, item.get("line_to"))
        _bad_from = item.get("line_from") is not None and s_first is None
        _bad_to = item.get("line_to") is not None and s_last is None
        if s_first is None:
            s_first = _next_sentence_after(sentences, _prev_last) or _prev_last
            if s_first is None:
                continue
        if s_last is None:
            s_last = _next_sentence_after(sentences, s_first) or sentences[-1]
        if int(s_first["seq"]) > int(s_last["seq"]):
            s_first, s_last = s_last, s_first
        if _prev_last is not None and int(s_first["seq"]) <= int(_prev_last["seq"]):
            s_first = _next_sentence_after(sentences, _prev_last) or _prev_last
            if int(s_last["seq"]) <= int(s_first["seq"]):
                s_last = _next_sentence_after(sentences, s_first) or sentences[-1]
        if seg_rows and int(s_first["seq"]) <= int(seg_rows[-1].end_sentence_seq):
            warnings.append(
                f"L3 段{item.get('seq')} 与段{seg_rows[-1].seq} 句子区间重叠（{s_first['seq']}≤{seg_rows[-1].end_sentence_seq}），harness 已合并跳过"
            )
            continue
        if _bad_from or _bad_to:
            warnings.append(
                f"L3 段{item.get('seq')} 模型行号非法，已由 harness 锚定到句 {s_first['seq']}~{s_last['seq']}"
            )
        start_ms, end_ms, _ = validate.correct_time(
            s_first["start_ms"], s_last["end_ms"], (int(s_first["start_ms"]), int(s_last["end_ms"]))
        )
        seg_type = str(item.get("seg_type") or "").strip()
        if seg_type not in validator.wl.get("seg_type", set()):
            seg_type = "铺垫"
        _existing_seqs = {s.seq for s in seg_rows}
        _new_seq = int(item.get("seq") or 0)
        while _new_seq in _existing_seqs:
            _new_seq += 1
        row = M.ScriptSegment(
            script_id=script.id,
            seq=_new_seq,
            seg_type=seg_type,
            title=(str(item.get("title") or "")[:128] or None),
            start_ms=start_ms,
            end_ms=end_ms,
            start_sentence_seq=int(s_first["seq"]),
            end_sentence_seq=int(s_last["seq"]),
            purpose=str(item.get("purpose") or "")[:4000] or "（缺失）",
            summary=str(item.get("summary") or "")[:4000] or "（缺失）",
            hook_point=1 if item.get("hook_point") else 0,
            payoff_point=1 if item.get("payoff_point") else 0,
            emotion_peak=_clamp_intensity(item.get("emotion_level")),
        )
        v = validator.validate(
            "script_segment",
            {
                **item,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "seg_type": seg_type,
                "start_sentence_seq": int(s_first["seq"]),
                "end_sentence_seq": int(s_last["seq"]),
            },
            label=f"seg{row.seq}:{row.title or ''}",
        )
        verdicts.append(v.as_dict())
        if not v.accepted:
            continue
        db.add(row)
        seg_rows.append(row)
        _prev_last = s_last

    # --- harness 兜底补段：口播充足时，尾部无口播画面段补齐 ---
    if asr_count >= 6 and seg_rows:
        last = seg_rows[-1]
        last_asr_end = max((int(u.get("end_ms") or 0) for u in units), default=0)
        if last.end_ms < dur_ms - 2000 and last_asr_end >= last.end_ms - 500:
            st = max(last.end_ms, last_asr_end)
            en = dur_ms
            if en - st >= 3000:
                _existing_seqs = {s.seq for s in seg_rows}
                _new_seq = 1
                while _new_seq in _existing_seqs:
                    _new_seq += 1
                row = M.ScriptSegment(
                    script_id=script.id,
                    seq=_new_seq,
                    seg_type="干货",
                    title=None,
                    start_ms=st,
                    end_ms=en,
                    start_sentence_seq=last.end_sentence_seq,
                    end_sentence_seq=last.end_sentence_seq,
                    purpose="口播结束后的画面段（画面演示/技巧展示/收尾），完整覆盖全片不丢尾",
                    summary="口播结束后的纯画面段（无口播，画面演示/收尾）",
                    hook_point=0,
                    payoff_point=0,
                    emotion_peak=last.emotion_peak,
                )
                db.add(row)
                seg_rows.append(row)
                warnings.append(
                    f"L3 harness 自动补纯画面段（{st}ms~{en}ms，口播结束后的画面段）"
                )

    # --- 碎片合并 ---
    if len(seg_rows) > 1:
        _i = 1
        while _i < len(seg_rows):
            _prev = seg_rows[_i - 1]
            _cur = seg_rows[_i]
            _n_cur = _cur.end_sentence_seq - _cur.start_sentence_seq + 1
            _cur_dur = _cur.end_ms - _cur.start_ms
            _contiguous = _cur.start_sentence_seq == _prev.end_sentence_seq + 1
            _gap = 10**9
            if _contiguous:
                _pl = next((s for s in sentences if int(s.get("seq") or 0) == _prev.end_sentence_seq), None)
                _cf = next((s for s in sentences if int(s.get("seq") or 0) == _cur.start_sentence_seq), None)
                if _pl is not None and _cf is not None:
                    _gap = int(_cf.get("start_ms") or 0) - int(_pl.get("end_ms") or 0)
            if _contiguous and _n_cur <= 1 and _cur_dur < 10000 and _gap < 1500:
                warnings.append(
                    f"L3 harness 合并碎片段：段{_cur.seq}（{_cur.start_ms/1000:.0f}s~{_cur.end_ms/1000:.0f}s）并入段{_prev.seq}"
                )
                _prev.end_ms = max(_prev.end_ms, _cur.end_ms)
                _prev.end_sentence_seq = max(_prev.end_sentence_seq, _cur.end_sentence_seq)
                seg_rows.pop(_i)
                await db.delete(_cur)  # 不吞异常：删除失败就抛出来，避免 session 里残留导致 UNIQUE 冲突
            else:
                _i += 1
        for _n, _s in enumerate(seg_rows, start=1):
            _s.seq = _n

    # --- BGM/氛围型 harness 峰值修复 ---
    if bgm_ambience and curve is not None and curve.intensity_series:
        _pts = curve.intensity_series
        if _pts and isinstance(_pts[0], str):
            try:
                _pts = json.loads(_pts[0])
            except Exception:
                _pts = []
        _n = len(seg_rows)
        for _i, _s in enumerate(seg_rows):
            _vals = [float(v) for (t, v) in _pts if _s.start_ms <= t <= _s.end_ms]
            if _vals:
                _base = sum(_vals) / len(_vals)
                _mult = 0.85 + (0.15 * _i / max(_n - 1, 1))
                _s.emotion_peak = round(min(_base * _mult, 10.0), 1)

    for i, s in enumerate(seg_rows, start=1):
        s.seq = i
    await db.flush()
    counts["script_segment"] = len(seg_rows)
    await db.commit()

    if not seg_rows:
        raise _PipelineAbort("L3 段落全部未通过校验")
    if len(seg_rows) < L3_SEGMENT_MIN:
        warnings.append(f"L3 落库段数 {len(seg_rows)} 低于契约下限 {L3_SEGMENT_MIN}")

    # 防御：按 start_ms 排序后重新编号 seq，彻底避免 LLM 返回重复 seq 导致 UNIQUE 冲突
    seg_rows.sort(key=lambda r: r.start_ms)
    for _i, _row in enumerate(seg_rows, start=1):
        _row.seq = _i

    def segment_of_line(seq: int) -> M.ScriptSegment | None:
        for seg in seg_rows:
            if seg.start_sentence_seq <= seq <= seg.end_sentence_seq:
                return seg
        return seg_rows[-1] if seg_rows else None

    return seg_rows, segment_of_line
