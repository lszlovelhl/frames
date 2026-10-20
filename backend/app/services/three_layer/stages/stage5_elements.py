"""Stage 5: L5 元素提炼（六类分库）与 L6 组合模板还原。
负责爆款积木提炼、R12/R15 定点重试与深度化、溯源绑定与 LibCombo 组合模板还原。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as M
from app.services.llm_json import complete_json
from app.services.three_layer import emotion, validate
from app.services.three_layer.prompts import LAYER_ARRAY_KEYS
from app.services.three_layer.stages.common import (
    Progress,
    _line_to_sentence,
    _placeholder_row,
    _run_layer,
)

logger = logging.getLogger(__name__)

R12_RETRY_SYSTEM = (
    "你是短视频拆解系统的元素质量修复器。给定一个已提取的元素和校验反馈，"
    "只修复【发散想象（imagination）】与【变体（variants）】两个字段，其他字段一律保持原样。\n"
    "要求：\n"
    "- imagination：用中文自然语言描述该元素可以如何迁移到其他场景/品类/人群/平台，"
    "必须 ≥30 字，落到具体可执行的场景细节，禁止空泛套话。\n"
    "- variants：2~3 条发散变体，每条是独立不重复的方向（互相相似度必须 <0.8），"
    "每条给出具体可执行的改写方向，不得只是换词。\n"
    "只输出 JSON：{\"imagination\": \"...\", \"variants\": [{\"text\": \"...\"}, ...]}"
)

DEEP_SYSTEM = (
    "你是短视频拆解系统的深度化器。给定一个已提取的元素及其来源上下文，把【机制（mechanism）】"
    "【发散想象（imagination）】【变体（variants）】三个字段从'短句标签'升级为'方法论级内容'。"
    "只重写这三个字段，其他字段一律保持原样。\n"
    "深度要求：\n"
    "- mechanism ≥60 字，写满四要素：①触发条件 ②点名具体心理或传播机制 ③位置关联 ④适用边界或反例。\n"
    "- imagination ≥50 字，写满三要素：①具体迁移场景 ②在该场景的具体改编动作 ③预期效果差异。\n"
    "- variants：2~3 条，每条 ≥15 字、互相不相似，必须是'场景 + 具体改法'。\n"
    "只输出 JSON：{\"mechanism\": \"...\", \"imagination\": \"...\", \"variants\": [{\"text\": \"...\"}, ...]}"
)


async def _retry_r12_fields(
    item: dict[str, Any], feedback: list[str], *, model: str
) -> dict[str, Any] | None:
    payload = {
        k: item.get(k)
        for k in ("code", "name", "text", "mechanism", "variants", "imagination")
        if item.get(k) is not None
    }
    user = (
        "【元素当前内容】\n"
        + json.dumps(payload, ensure_ascii=False)
        + "\n\n【校验反馈】\n"
        + "\n".join(f"- {f}" for f in feedback)
        + "\n\n按要求修复后，只输出 imagination 与 variants 两个字段的 JSON。"
    )
    try:
        data, _ = await complete_json(
            [
                {"role": "system", "content": R12_RETRY_SYSTEM},
                {"role": "user", "content": user},
            ],
            array_keys=["imagination", "variants"],
            model=model,
            max_tokens=4096,
            scene="r12_retry",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("L5 R12 定点重试调用失败：%s", exc)
        return None
    if not data:
        return None
    out: dict[str, Any] = {}
    if isinstance(data.get("imagination"), str) and len(data["imagination"]) >= 30:
        out["imagination"] = data["imagination"][:4000]
    vs = data.get("variants")
    if isinstance(vs, list) and len(vs) >= 2:
        texts = [str(x.get("text") if isinstance(x, dict) else x) for x in vs]
        dup = any(
            validate.similarity(texts[i], texts[j]) >= 0.8
            for i in range(len(texts))
            for j in range(i + 1, len(texts))
        )
        if not dup:
            out["variants"] = [x for x in vs][:4]
    return out or None


async def _r12_retry_and_validate(
    db: AsyncSession,
    video: M.Video,
    analysis_id: str,
    model: str,
    validator: validate.Validator,
    table: str,
    label: str,
    item: dict[str, Any],
    values: dict[str, Any],
    v: validate.Verdict,
    refs: list[dict[str, Any]],
) -> tuple[bool, Any, dict[str, Any]]:
    r12 = [s for s in v.soft if s.startswith("R12")]
    if not r12 or v.status == "rejected":
        return v.accepted, v, values
    fixed = await _retry_r12_fields(item, r12, model=model)
    if not fixed:
        return v.accepted, v, values
    values = {**values, **fixed}
    v2 = validator.validate(table, {**item, **values}, label=label, refs=refs)
    if v2.accepted:
        return True, v2, values
    logger.info("L5 R12 定点重试后仍不达标：%s（%s）", label, [s for s in v2.soft if s.startswith("R12")])
    return v.accepted, v, values


async def _deepen_element(
    item: dict[str, Any], issues: list[str], context: dict[str, Any], *, model: str
) -> dict[str, Any] | None:
    payload = {
        k: item.get(k)
        for k in ("code", "name", "text", "mechanism", "variants", "imagination")
        if item.get(k) is not None
    }
    user = (
        "【元素当前内容】\n"
        + json.dumps(payload, ensure_ascii=False)
        + "\n\n【深度问题反馈】\n"
        + "\n".join(f"- {f}" for f in issues)
        + "\n\n【来源上下文（供位置关联与机制分析）】\n"
        + json.dumps(context, ensure_ascii=False)
        + "\n\n按要求深度化后，只输出 mechanism、imagination 与 variants 三个字段的 JSON。"
    )
    try:
        data, _ = await complete_json(
            [
                {"role": "system", "content": DEEP_SYSTEM},
                {"role": "user", "content": user},
            ],
            array_keys=["mechanism", "imagination", "variants"],
            model=model,
            max_tokens=4096,
            scene="r15_deepen",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("L5 R15 定点深度化调用失败：%s", exc)
        return None
    if not data:
        return None
    out: dict[str, Any] = {}
    m = str(data.get("mechanism") or "")
    if m and not validate.is_shallow_mechanism(m):
        out["mechanism"] = m[:4000]
    img = str(data.get("imagination") or "")
    if img and not validate.is_shallow_imagination(img):
        out["imagination"] = img[:4000]
    vs = data.get("variants")
    if isinstance(vs, list) and len(vs) >= 2:
        texts = [str(x.get("text") if isinstance(x, dict) else x) for x in vs]
        dup = any(
            validate.similarity(texts[i], texts[j]) >= 0.8
            for i in range(len(texts))
            for j in range(i + 1, len(texts))
        )
        shallow = any(len(t) < 15 for t in texts)
        if not dup and not shallow:
            out["variants"] = [x for x in vs][:4]
    return out or None


async def run_stage5_elements(
    db: AsyncSession,
    video: M.Video,
    script: M.ScriptScript,
    *,
    analysis_id: str,
    duration_ms: int,
    ctx: dict[str, Any],
    sentences: list[dict[str, Any]],
    sentence_rows: list[M.ScriptSentence],
    seg_rows: list[M.ScriptSegment],
    segment_of_line: Callable[[int], M.ScriptSegment | None],
    curve_grid: list[int],
    curve_values: list[float],
    stats: dict[str, Any],
    interval_ms: int,
    model: str,
    validator: validate.Validator,
    quality: dict[str, Any],
    counts: dict[str, int],
    warnings: list[str],
    verdicts: list[dict[str, Any]],
    report: Progress,
) -> tuple[dict[str, int], M.LibCombo | None, list[M.LibComboSlot]]:
    """执行 L5 爆款元素提炼与 L6 组合模板生成。

    返回: (lib_counts, combo_row, slot_rows)
    """
    await report("L5", 80, "三层链路 · L5 积木提炼（六类分库）…")
    l5_user = (
        "【L4 句级脚本（seq / 原话 / 机制 / 手法引用 / 强度）】\n"
        + json.dumps(
            [
                {
                    "seq": r.seq,
                    "quote": r.quote,
                    "function": r.sentence_function,
                    "function_reason": r.function_reason,
                    "method_refs": r.method_refs,
                    "emotion_intensity": r.emotion_intensity,
                }
                for r in sentence_rows
            ],
            ensure_ascii=False,
        )
        + f"\n\n【中心思想】{script.core_idea}\n【内容走向】{script.content_trend}\n【目标人群】{script.target_audience}"
    )

    lib_targets = (
        ("topics", "选题库"),
        ("hooks", "钩子库"),
        ("copywriting", "文案库"),
        ("quotes", "金句库"),
        ("methods", "手法库"),
    )
    data5: dict[str, Any] = {}
    q5_agg: dict[str, Any] = {"arrays": {}, "prompt_version": None, "errors": []}
    for key, label in lib_targets:
        await report("L5", 80, f"三层链路 · L5 积木提炼（{label}）…")
        ok_part, part, q5 = await _run_layer(
            db,
            video=video,
            analysis_id=analysis_id,
            layer=5,
            user_text=l5_user + f"\n\n【本次任务】只输出 {key} 数组（{label}），其它数组一律不要输出。",
            model=model,
            array_keys=[key],
            max_tokens=16384,
        )
        q5_agg["prompt_version"] = q5.get("prompt_version")
        if ok_part:
            data5[key] = list(part.get(key) or [])
            q5_agg["arrays"][key] = len(data5[key])
        else:
            q5_agg["errors"].append(f"{label}：{part.get('layer_error')}")
    quality["L5"] = q5_agg
    ok5 = any(q5_agg["arrays"].values())

    lib_counts: dict[str, int] = {}
    topic_rows: list[M.Any] = []
    hook_rows: list[M.Any] = []
    copy_rows: list[M.Any] = []
    quote_rows: list[M.Any] = []
    method_rows: list[M.Any] = []

    def _ref(source_line: Any, lib_row: Any, element_table: str) -> M.RefElementSource | None:
        raw = _line_to_sentence(sentences, source_line)
        if raw is None:
            return None
        sent_row = next((r for r in sentence_rows if int(r.seq) == int(raw["seq"])), None)
        return M.RefElementSource(
            element_table=element_table,
            element_id=lib_row.id,
            source_script_id=script.id,
            source_video_id=str(video.id),
            source_sentence_id=(sent_row.id if sent_row else None),
            source_segment_id=(segment_of_line(int(raw["seq"])).id if segment_of_line(int(raw["seq"])) else None),
            quote=str(raw["text"])[:4000],
            start_ms=int(raw["start_ms"]),
            end_ms=int(raw["end_ms"]),
            source_platform=ctx["platform"][:32] if ctx.get("platform") else None,
        )

    async def _upsert_lib(model_cls, code: str, values: dict[str, Any], source_video_id: str | None = None):
        if source_video_id:
            await db.execute(
                delete(model_cls).where(
                    model_cls.code == code,
                    model_cls.source_video_id != source_video_id,
                )
            )
        stmt = select(model_cls).where(model_cls.code == code)
        if source_video_id:
            stmt = stmt.where(model_cls.source_video_id == source_video_id)
        existing = (await db.execute(stmt)).scalar_one_or_none()
        if existing is None:
            row = model_cls(code=code, source_video_id=source_video_id, **values)
            db.add(row)
            await db.flush()
            return row, False
        for k, v in values.items():
            setattr(existing, k, v)
        await db.flush()
        return existing, True

    def _deepen_context(item: dict[str, Any], ref: M.RefElementSource) -> dict[str, Any]:
        raw = _line_to_sentence(sentences, item.get("source_line_no"))
        sent = next((r for r in sentence_rows if raw and int(r.seq) == int(raw["seq"])), None)
        seg = segment_of_line(int(raw["seq"])) if raw else None
        dur = int(script.duration_ms or 0) or 1
        return {
            "证据原话": str(getattr(ref, "quote", "") or "")[:400],
            "秒数": f"{int(getattr(ref, 'start_ms', 0) or 0)}ms → {int(getattr(ref, 'end_ms', 0) or 0)}ms",
            "来源句功能": str(sent.sentence_function if sent else "") or "",
            "来源句情绪强度": str(sent.emotion_intensity if sent else "") or "",
            "所在段落": f"{seg.seg_type if seg else ''}｜{seg.title if seg else ''}" if seg else "",
            "目标人群": str(script.target_audience or "")[:200],
            "中心思想": str(script.core_idea or "")[:200],
        }

    async def _deepen_and_validate(
        table: str, label: str, item: dict[str, Any], values: dict[str, Any], v, ref: M.RefElementSource
    ) -> tuple[dict[str, Any], Any]:
        r15 = [s for s in v.soft if s.startswith("R15")]
        if not r15 or v.status == "rejected":
            return values, v
        refs = [{"quote": ref.quote, "start_ms": ref.start_ms, "end_ms": ref.end_ms}]
        fixed = await _deepen_element(item, r15, _deepen_context(item, ref), model=model)
        if not fixed:
            return values, v
        values2 = {**values, **fixed}
        v2 = validator.validate(table, {**item, **values2}, label=label, refs=refs)
        if v2.accepted:
            return values2, v2
        return values, v

    if ok5:
        # Topics
        for item in data5.get("topics") or []:
            code = str(item.get("code") or "").strip().lower()
            if not validate.is_code(code):
                continue
            values = dict(
                name=str(item.get("name") or code)[:128],
                topic_type=str(item.get("topic_type") or "痛点型"),
                audience=str(item.get("audience") or ""),
                pain_point=str(item.get("pain_point") or ""),
                angle=str(item.get("angle") or ""),
                value_type=str(item.get("value_type") or "情绪"),
                applicable_category=(str(script.category or "")[:64] or None),
                keywords=list(item.get("keywords") or []),
                mechanism=str(item.get("mechanism") or "")[:4000],
                variants=list(item.get("variants") or []),
                imagination=str(item.get("imagination") or "")[:4000],
                status="active",
                quality_score=None,
            )
            if values["value_type"] not in validator.wl.get("value_type", set()):
                values["value_type"] = "情绪"
            if values["topic_type"] not in validator.wl.get("topic_type", set()):
                values["topic_type"] = "痛点型"
            tmp = _placeholder_row()
            ref = _ref(item.get("source_line_no"), tmp, "lib_topic")
            if ref is None:
                continue
            v = validator.validate(
                "lib_topic",
                {**item, **values},
                label=code,
                refs=[{"quote": ref.quote, "start_ms": ref.start_ms, "end_ms": ref.end_ms}],
            )
            accepted, v, values = await _r12_retry_and_validate(
                db,
                video,
                analysis_id,
                model,
                validator,
                "lib_topic",
                code,
                item,
                values,
                v,
                [{"quote": ref.quote, "start_ms": ref.start_ms, "end_ms": ref.end_ms}],
            )
            if not accepted:
                verdicts.append(v.as_dict())
                continue
            values, v = await _deepen_and_validate("lib_topic", code, item, values, v, ref)
            row, _ = await _upsert_lib(
                M.LibTopic,
                code,
                {
                    **values,
                    "quality_score": v.quality_score,
                    "status": "active",
                    "review_status": "accepted" if v.status == "active" else "draft",
                },
                source_video_id=str(video.id),
            )
            ref.element_id = row.id
            db.add(ref)
            verdicts.append(v.as_dict())
            topic_rows.append(row)
        lib_counts["lib_topic"] = len(topic_rows)

        # Hooks
        for item in data5.get("hooks") or []:
            code = str(item.get("code") or "").strip().lower()
            if not validate.is_code(code):
                continue
            hook_type = str(item.get("hook_type") or "结果前置")
            if hook_type not in validator.wl.get("hook_type", set()):
                hook_type = "结果前置"
            values = dict(
                name=str(item.get("name") or code)[:128],
                hook_type=hook_type,
                position=str(item.get("position") or "前3秒"),
                sentence_pattern=str(item.get("sentence_pattern") or ""),
                variables=list(item.get("variables") or []),
                expected_effect=str(item.get("expected_effect") or ""),
                mechanism=str(item.get("mechanism") or "")[:4000],
                variants=list(item.get("variants") or []),
                imagination=str(item.get("imagination") or "")[:4000],
                status="active",
                quality_score=None,
            )
            if values["position"] not in validator.wl.get("hook_position", set()):
                values["position"] = "前3秒"
            tmp = _placeholder_row()
            ref = _ref(item.get("source_line_no"), tmp, "lib_hook")
            if ref is None:
                continue
            v = validator.validate(
                "lib_hook",
                {**item, **values},
                label=code,
                refs=[{"quote": ref.quote, "start_ms": ref.start_ms, "end_ms": ref.end_ms}],
            )
            accepted, v, values = await _r12_retry_and_validate(
                db,
                video,
                analysis_id,
                model,
                validator,
                "lib_hook",
                code,
                item,
                values,
                v,
                [{"quote": ref.quote, "start_ms": ref.start_ms, "end_ms": ref.end_ms}],
            )
            if not accepted:
                verdicts.append(v.as_dict())
                continue
            values, v = await _deepen_and_validate("lib_hook", code, item, values, v, ref)
            row, _ = await _upsert_lib(
                M.LibHook,
                code,
                {
                    **values,
                    "quality_score": v.quality_score,
                    "status": "active",
                    "review_status": "accepted" if v.status == "active" else "draft",
                },
                source_video_id=str(video.id),
            )
            ref.element_id = row.id
            db.add(ref)
            verdicts.append(v.as_dict())
            hook_rows.append(row)
        lib_counts["lib_hook"] = len(hook_rows)

        # Copywriting
        for item in data5.get("copywriting") or []:
            code = str(item.get("code") or "").strip().lower()
            if not validate.is_code(code):
                continue
            rhetoric = str(item.get("rhetoric") or "")
            if rhetoric and rhetoric not in validator.wl.get("rhetoric", set()):
                rhetoric = ""
            values = dict(
                name=str(item.get("name") or code)[:128],
                copy_type=str(item.get("copy_type") or "口播"),
                sentence_pattern=str(item.get("sentence_pattern") or ""),
                rhetoric=rhetoric,
                example_text=str(item.get("example_text") or "")[:4000],
                mechanism=str(item.get("mechanism") or "")[:4000],
                variants=list(item.get("variants") or []),
                imagination=str(item.get("imagination") or "")[:4000],
                status="active",
                quality_score=None,
            )
            if values["copy_type"] not in validator.wl.get("copy_type", set()):
                values["copy_type"] = "口播"
            tmp = _placeholder_row()
            ref = _ref(item.get("source_line_no"), tmp, "lib_copywriting")
            if ref is None:
                continue
            v = validator.validate(
                "lib_copywriting",
                {**item, **values},
                label=code,
                refs=[{"quote": ref.quote, "start_ms": ref.start_ms, "end_ms": ref.end_ms}],
            )
            accepted, v, values = await _r12_retry_and_validate(
                db,
                video,
                analysis_id,
                model,
                validator,
                "lib_copywriting",
                code,
                item,
                values,
                v,
                [{"quote": ref.quote, "start_ms": ref.start_ms, "end_ms": ref.end_ms}],
            )
            if not accepted:
                verdicts.append(v.as_dict())
                continue
            values, v = await _deepen_and_validate("lib_copywriting", code, item, values, v, ref)
            row, _ = await _upsert_lib(
                M.LibCopywriting,
                code,
                {
                    **values,
                    "quality_score": v.quality_score,
                    "status": "active",
                    "review_status": "accepted" if v.status == "active" else "draft",
                },
                source_video_id=str(video.id),
            )
            ref.element_id = row.id
            db.add(ref)
            verdicts.append(v.as_dict())
            copy_rows.append(row)
        lib_counts["lib_copywriting"] = len(copy_rows)

        # Quotes
        for item in data5.get("quotes") or []:
            code = str(item.get("code") or "").strip().lower()
            if not validate.is_code(code):
                continue
            values = dict(
                text=str(item.get("text") or "")[:4000],
                structure=str(item.get("structure") or ""),
                rewrite_template=str(item.get("rewrite_template") or ""),
                applicable_scene=str(item.get("applicable_scene") or ""),
                mechanism=str(item.get("mechanism") or "")[:4000],
                variants=list(item.get("variants") or []),
                imagination=str(item.get("imagination") or "")[:4000],
                status="active",
                quality_score=None,
            )
            tmp = _placeholder_row()
            ref = _ref(item.get("source_line_no"), tmp, "lib_quote")
            if ref is None:
                continue
            v = validator.validate(
                "lib_quote",
                {**item, **values},
                label=code,
                refs=[{"quote": ref.quote, "start_ms": ref.start_ms, "end_ms": ref.end_ms}],
            )
            accepted, v, values = await _r12_retry_and_validate(
                db,
                video,
                analysis_id,
                model,
                validator,
                "lib_quote",
                code,
                item,
                values,
                v,
                [{"quote": ref.quote, "start_ms": ref.start_ms, "end_ms": ref.end_ms}],
            )
            if not accepted:
                verdicts.append(v.as_dict())
                continue
            values, v = await _deepen_and_validate("lib_quote", code, item, values, v, ref)
            row, _ = await _upsert_lib(
                M.LibQuote,
                code,
                {
                    **values,
                    "quality_score": v.quality_score,
                    "status": "active",
                    "review_status": "accepted" if v.status == "active" else "draft",
                },
                source_video_id=str(video.id),
            )
            ref.element_id = row.id
            db.add(ref)
            verdicts.append(v.as_dict())
            quote_rows.append(row)
        lib_counts["lib_quote"] = len(quote_rows)

        # Methods
        for item in data5.get("methods") or []:
            code = str(item.get("code") or "").strip().lower()
            if not validate.is_code(code):
                continue
            category = str(item.get("category") or "结构")
            if category not in validator.wl.get("method_category", set()):
                category = "结构"
            level = str(item.get("abstraction_level") or "段落级")
            if level not in validator.wl.get("abstraction_level", set()):
                level = "段落级"
            values = dict(
                name=str(item.get("name") or code)[:128],
                category=category,
                controlled_tag=str(item.get("controlled_tag") or "")[:32],
                is_emergent=0,
                emergent_parent_code=None,
                mechanism=str(item.get("mechanism") or "")[:4000],
                abstraction_level=level,
                usage_steps=str(item.get("usage_steps") or "")[:4000],
                counter_example=str(item.get("counter_example") or "")[:4000],
                variants=list(item.get("variants") or []),
                imagination=str(item.get("imagination") or "")[:4000],
                status="active",
                quality_score=None,
            )
            tmp = _placeholder_row()
            ref = _ref(item.get("source_line_no"), tmp, "lib_method")
            if ref is None:
                continue
            v = validator.validate(
                "lib_method",
                {**item, **values},
                label=code,
                refs=[{"quote": ref.quote, "start_ms": ref.start_ms, "end_ms": ref.end_ms}],
            )
            accepted, v, values = await _r12_retry_and_validate(
                db,
                video,
                analysis_id,
                model,
                validator,
                "lib_method",
                code,
                item,
                values,
                v,
                [{"quote": ref.quote, "start_ms": ref.start_ms, "end_ms": ref.end_ms}],
            )
            if not accepted:
                verdicts.append(v.as_dict())
                continue
            values, v = await _deepen_and_validate("lib_method", code, item, values, v, ref)
            row, _ = await _upsert_lib(
                M.LibMethod,
                code,
                {
                    **values,
                    "quality_score": v.quality_score,
                    "status": "active",
                    "review_status": "accepted" if v.status == "active" else "draft",
                },
                source_video_id=str(video.id),
            )
            ref.element_id = row.id
            db.add(ref)
            verdicts.append(v.as_dict())
            method_rows.append(row)
        lib_counts["lib_method"] = len(method_rows)

    await db.flush()
    counts.update(lib_counts)
    await db.commit()

    # ---------- L6 组合模板还原 ----------
    for _t in ("lib_topic", "lib_hook", "lib_copywriting", "lib_quote", "lib_method", "lib_combo", "lib_combo_slot"):
        counts.setdefault(_t, 0)
    await report("L6", 90, "三层链路 · L6 组合模板还原…")
    method_codes = [r.code for r in method_rows]
    l6_user = (
        f"【L3 段落（seq/类型/标题/覆盖句区间/时长）】\n"
        + json.dumps(
            [
                {
                    "seq": s.seq,
                    "seg_type": s.seg_type,
                    "title": s.title,
                    "start_sentence_seq": s.start_sentence_seq,
                    "end_sentence_seq": s.end_sentence_seq,
                    "start_ms": s.start_ms,
                    "end_ms": s.end_ms,
                    "emotion_peak": s.emotion_peak,
                }
                for s in seg_rows
            ],
            ensure_ascii=False,
        )
        + f"\n\n【L5 手法库清单】\n{json.dumps(method_codes, ensure_ascii=False) if method_codes else '（无）'}"
        + f"\n\n【情绪曲线】采样间隔 {interval_ms}ms，形状 {stats.get('shape')}，峰值落点 {stats.get('peak_position_ratio')}，"
        + f"峰值 {stats.get('series_max')}，基线 {stats.get('baseline_intensity')}"
        + f"\n【中心思想】{script.core_idea}\n【内容走向】{script.content_trend}"
        + "\n要求：槽位与段落一一对应（segment_seq 必须取自上面的 seq），槽位区间首尾相接覆盖 [0,1]。"
    )
    ok6, data6, q6 = await _run_layer(
        db,
        video=video,
        analysis_id=analysis_id,
        layer=6,
        user_text=l6_user,
        model=model,
        array_keys=LAYER_ARRAY_KEYS[6],
        max_tokens=16384,
    )
    quality["L6"] = q6

    combo_data = dict(data6.get("combo") or {})
    slots_json = list(data6.get("slots") or [])
    combo_row: M.LibCombo | None = None
    slot_rows: list[M.LibComboSlot] = []

    if combo_data and slots_json:
        seg_total = sum(max(1, s.end_ms - s.start_ms) for s in seg_rows) or 1
        cursor = 0.0
        fixed = swap = 0
        for i, item in enumerate(
            sorted(slots_json, key=lambda x: float(x.get("position_ratio_start") or 0)), start=1
        ):
            seg = next(
                (s for s in seg_rows if int(s.seq) == int(item.get("segment_seq") or i)),
                seg_rows[min(i - 1, len(seg_rows) - 1)],
            )
            share = round(max(1, seg.end_ms - seg.start_ms) / seg_total, 4)
            start_ratio = round(cursor, 4)
            cursor = 1.0 if i == len(slots_json) else round(min(1.0, cursor + share), 4)
            role = str(item.get("slot_role") or "").strip()
            if role not in ("固定", "可替换"):
                role = "可替换"
            if role == "固定":
                fixed += 1
            else:
                swap += 1
            slot_rows.append(
                M.LibComboSlot(
                    combo_id=None,
                    seq=i,
                    slot_role=role,
                    method_id=None,
                    method_code=str(item.get("method_code") or f"method.seq.{i}")[:64],
                    expected_function=str(item.get("expected_function") or "")[:4000] or "（缺失）",
                    position_ratio_start=start_ratio,
                    position_ratio_end=cursor,
                    duration_ratio=round(cursor - start_ratio, 4),
                    swap_alternatives=list(item.get("swap_alternatives") or []),
                )
            )

        code = str(combo_data.get("code") or "").strip().lower() or f"combo.auto.{script.id.hex[:6]}"
        if not validate.is_code(code):
            code = f"combo.auto.{script.id.hex[:6]}"
        curve_points: list[list[float]] = []
        for i, slot in enumerate(slot_rows):
            end_ms = int(seg_rows[min(i, len(seg_rows) - 1)].end_ms) if seg_rows else duration_ms
            idx = emotion.index_of_t(curve_grid, end_ms)
            curve_points.append([round(float(slot.position_ratio_end), 4), round(float(curve_values[idx]), 1)])
        if isinstance(data6.get("emotion_curve"), list) and len(data6["emotion_curve"]) == len(slot_rows):
            curve_points = data6["emotion_curve"]
        shape = str(combo_data.get("emotion_shape") or stats.get("shape"))
        if shape not in validator.wl.get("shape", set()):
            shape = stats.get("shape")
        intent = str(combo_data.get("intent") or "情绪共鸣")
        if intent not in validator.wl.get("intent", set()):
            intent = "情绪共鸣"
        duration_ratio = [round(float(s.duration_ratio), 4) for s in slot_rows]
        combo_values = dict(
            name=str(combo_data.get("name") or code)[:128],
            intent=intent,
            core_idea_alignment=str(combo_data.get("core_idea_alignment") or "")[:4000] or "（缺失）",
            content_trend=str(combo_data.get("content_trend") or script.content_trend)[:4000],
            sequence_desc=str(combo_data.get("sequence_desc") or "")[:4000] or "（缺失）",
            emotion_shape=shape,
            emotion_curve=curve_points,
            duration_ratio=duration_ratio,
            applicable_category=(str(script.category or "")[:64] or None),
            slot_count=len(slot_rows),
            fixed_slot_count=fixed,
            swap_slot_count=swap,
            mechanism=str(combo_data.get("mechanism") or "")[:4000],
            variants=list(combo_data.get("variants") or []),
            imagination=str(combo_data.get("imagination") or "")[:4000],
            status="active",
            quality_score=None,
        )
        combo_payload = {**combo_data, **combo_values, "emotion_curve": curve_points}
        slot_payload = [
            {
                "slot_role": s.slot_role,
                "position_ratio_start": s.position_ratio_start,
                "position_ratio_end": s.position_ratio_end,
            }
            for s in slot_rows
        ]
        ref = _ref(seg_rows[0].start_sentence_seq if seg_rows else None, _placeholder_row(), "lib_combo")
        ref_payload = (
            [{"quote": ref.quote, "start_ms": ref.start_ms, "end_ms": ref.end_ms}] if ref is not None else []
        )
        v = validator.validate("lib_combo", combo_payload, label=code, refs=ref_payload)
        validator.r14_combo(combo_payload, slot_payload, v)
        verdicts.append(v.as_dict())
        if v.accepted:
            combo_row, _ = await _upsert_lib(
                M.LibCombo,
                code,
                {
                    **combo_values,
                    "quality_score": v.quality_score,
                    "status": "active",
                    "review_status": "accepted" if v.status == "active" else "draft",
                },
                source_video_id=str(video.id),
            )
            if ref is not None:
                ref.element_id = combo_row.id
                db.add(ref)
            await db.execute(delete(M.LibComboSlot).where(M.LibComboSlot.combo_id == combo_row.id))
            await db.flush()
            for slot in slot_rows:
                slot.combo_id = combo_row.id
                db.add(slot)
            await db.flush()
            counts["lib_combo"] = 1
            counts["lib_combo_slot"] = len(slot_rows)
    else:
        warnings.append("L6 未返回可用组合模板（lib_combo 未写入）")

    return lib_counts, combo_row, slot_rows
