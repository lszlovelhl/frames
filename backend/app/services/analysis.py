"""五层拆解编排服务

素材输入（MVP）：视频元数据 + 用户提供的字幕/旁白文本。
流程：逐层调 AI（json_mode），产出解析后落库：
  L1 topline          → analysis.summary + analysis_layers
  L2 snapshot         → analysis_layers
  L3 structure        → segments + analysis_layers
  L4 refine           → analysis_notes + analysis_layers
  L5 element_extract  → elements + analysis_layers

单层失败不阻塞后续层：错误以 {"layer_error": ...} 写入该层 content。
"""
import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as M
from app.ai import chat

logger = logging.getLogger(__name__)

LAYER_CODES = {
    1: "layer1_topline",
    2: "layer2_snapshot",
    3: "layer3_structure",
    4: "layer4_refine",
    5: "layer5_element_extract",
}

# 各层希望 AI 输出的 JSON 结构（同时作为解析器契约）
LAYER_SCHEMAS: dict[int, dict[str, Any]] = {
    1: {
        "one_liner": "全片一句话结论",
        "topic": "选题主题",
        "hook_hypothesis": "钩子如何设计及为何有效",
        "expected_structure": ["预期段落"],
        "target_audience": "目标人群",
        "key_perspective": "差异化观点/情绪切入点",
    },
    2: {
        "rhythm": "节奏总评",
        "emotion_curve": [{"phase": "阶段名", "level": 0}],
        "pacing_notes": "信息密度与节奏手法",
        "platform_notes": "平台属性/运营痕迹观察",
        "retention_hypothesis": "可能的完播/停留设计",
    },
    3: {
        "segments": [
            {
                "seq": 1,
                "type": "钩子/铺垫/冲突/转折/高潮/干货/CTA",
                "title": "段标题",
                "start_ms": 0,
                "end_ms": 0,
                "hook_point": False,
                "payoff_point": False,
                "emotion_level": 5,
                "summary": "段落作用与手法",
            }
        ]
    },
    4: {
        "notes": [
            {
                "note_type": "transcript/shot/audio/text_overlay/rhythm/frame",
                "start_ms": 0,
                "end_ms": 0,
                "role_view": "编导/运营/拍摄/剪辑/全员",
                "content": "具体观察，注明原话引用与为何有效/可学",
                "confidence": 0.7,
            }
        ]
    },
    5: {
        "elements": [
            {
                "category": "选题/钩子/结构/话术/情绪/视觉/剪辑手法/声音设计/运营策略",
                "name": "元素名",
                "description": "一句话说明",
                "formula": "可复用配方（步骤化）",
                "role_view": "受益岗位",
                "confidence": 0.7,
                "evidence": [{"seg": 1, "quote": "原文/画面证据"}],
            }
        ]
    },
}


def _clean_int(value: Any, default: int = 0) -> int:
    try:
        return max(int(float(value)), 0)
    except (TypeError, ValueError):
        return default


def _parse_json_text(text: str) -> dict:
    """剥离代码块后解析 JSON；失败返回空 dict。"""
    if not text:
        return {}
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    try:
        data = json.loads(cleaned)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        # 尝试截取首个 { ... } 段
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(cleaned[start : end + 1])
                return data if isinstance(data, dict) else {}
            except json.JSONDecodeError:
                return {}
        return {}


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


def _build_video_material(video: M.Video, subtitle_text: str, media_brief: str = "") -> str:
    """视频侧信息 + 字幕素材 + 多模态简报 → 拼给模型的 user 内容。"""
    parts = [
        f"平台：{video.platform or '未知'}",
        f"标题：{video.title}",
        f"作者：{video.author_name or '未知'}",
        f"链接：{video.url}",
        f"发布时间：{video.publish_time or '未知'}",
    ]
    if video.tags:
        parts.append(f"标签：{' / '.join(video.tags)}")
    if video.stats_snapshot:
        parts.append(f"数据快照：{json.dumps(video.stats_snapshot, ensure_ascii=False)}")
    if media_brief:
        parts.append(media_brief)
    if subtitle_text:
        parts.append(f"\n语音转写/旁白全文（用于逐句拆解）:\n{subtitle_text}")
    return "\n".join(parts)


async def _load_prompt(db: AsyncSession, code: str) -> M.PromptTemplate | None:
    """取该 code 最新 active 模板。"""
    stmt = (
        select(M.PromptTemplate)
        .where(M.PromptTemplate.code == code, M.PromptTemplate.status == "active")
        .order_by(M.PromptTemplate.version.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def _run_layer(
    db: AsyncSession,
    analysis: M.Analysis,
    video: M.Video,
    subtitle_text: str,
    model: str,
    layer: int,
    previous_summary: str,
    media_brief: str = "",
) -> tuple[bool, dict, str]:
    """执行单层拆解；返回 (ok, content, raw)。"""
    template = await _load_prompt(db, LAYER_CODES[layer])
    if template is None:
        content = {"layer_error": f"缺少模板 {LAYER_CODES[layer]}，请先跑种子脚本"}
        await db.flush()
        return False, content, ""

    schema_hint = json.dumps(LAYER_SCHEMAS[layer], ensure_ascii=False, indent=1)
    user_text = _build_video_material(video, subtitle_text, media_brief)
    if previous_summary:
        user_text += f"\n\n【前序层结论，供引用】\n{previous_summary}"
    user_text += f'\n\n必须严格输出 JSON 对象，字段结构如下（不得增删字段名，无法确定的时间写 0）：\n{schema_hint}'

    messages = [
        {"role": "system", "content": template.content},
        {"role": "user", "content": user_text},
    ]
    # 推理型模型会把 reasoning 计入 max_tokens，复杂层易吃光配额导致 content 为空 → 失败换温度重试一次
    last_exc: str | None = None
    for attempt in range(2):
        try:
            temperature = 0.2 if layer < 5 else 0.4
            if attempt == 1:
                temperature += 0.4  # 二次重试升温，打破缓存导致的同构失败
            result = await chat(
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=8192,
                json_mode=True,
                timeout=240,
                scene="breakdown",
                ref_type="analysis",
                ref_id=str(analysis.id),
            )
            raw = result.get("reply") or ""
            if raw.strip():
                break
            last_exc = f"第{attempt + 1}次返回空 content（reasoning 可能超配额）"
        except Exception as exc:  # noqa: BLE001
            last_exc = str(exc)
            logger.warning("layer %s AI 调用失败(attempt %s): %s", layer, attempt + 1, exc)
    else:
        content = {"layer_error": f"AI 调用失败: {last_exc}"}
        layer_row = M.AnalysisLayer(
            analysis_id=analysis.id,
            layer=layer,
            step="main",
            role_view="全员",
            content=content,
            source_type="inferred",
            model=model,
            prompt_version=template.version,
            raw_response="",
        )
        db.add(layer_row)
        analysis.current_layer = layer
        await db.flush()
        return False, content, ""

    content = _parse_json_text(raw)
    if not content:
        content = {"layer_error": "模型未返回可解析 JSON", "raw_preview": raw[:500]}

    layer_row = M.AnalysisLayer(
        analysis_id=analysis.id,
        layer=layer,
        step="main",
        role_view="全员",
        content=content,
        source_type="inferred",
        model=result.get("model"),
        prompt_version=template.version,
        raw_response=raw,
    )
    db.add(layer_row)
    analysis.current_layer = layer
    await db.flush()
    return bool(content), content, raw


async def _persist_layer_artifacts(
    db: AsyncSession,
    analysis: M.Analysis,
    layer: int,
    content: dict,
) -> None:
    """把结构层产物落库（L3 segments / L4 notes / L5 elements）。"""
    if layer == 3:
        for seg in content.get("segments", []) or []:
            db.add(
                M.Segment(
                    analysis_id=analysis.id,
                    seq=_clean_int(seg.get("seq"), len(content.get("segments", []))),
                    seg_type=str(seg.get("type") or "段落"),
                    title=seg.get("title"),
                    start_ms=_clean_int(seg.get("start_ms")),
                    end_ms=_clean_int(seg.get("end_ms")),
                    hook_point=bool(seg.get("hook_point")),
                    payoff_point=bool(seg.get("payoff_point")),
                    emotion_curve=seg.get("emotion_level"),
                    summary=seg.get("summary"),
                    role_view="编导",
                )
            )
    elif layer == 4:
        for note in content.get("notes", []) or []:
            db.add(
                M.AnalysisNote(
                    analysis_id=analysis.id,
                    note_type=str(note.get("note_type") or "transcript"),
                    start_ms=_clean_int(note.get("start_ms")),
                    end_ms=_clean_int(note.get("end_ms")),
                    role_view=str(note.get("role_view") or "全员"),
                    content=str(note.get("content") or ""),
                    confidence=note.get("confidence"),
                )
            )
    elif layer == 5:
        for elem in content.get("elements", []) or []:
            db.add(
                M.Element(
                    analysis_id=analysis.id,
                    category=str(elem.get("category") or "手法"),
                    name=str(elem.get("name") or "未命名元素"),
                    description=elem.get("description"),
                    formula=elem.get("formula"),
                    source_type="inferred",
                    confidence=elem.get("confidence"),
                    role_view=str(elem.get("role_view") or "全员"),
                    evidence=elem.get("evidence") or [],
                    status="draft",
                )
            )


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


async def run_analysis(
    db: AsyncSession,
    video: M.Video,
    subtitle_text: str,
    model: str = "flash",
    target_layers: int = 5,
    media_brief: str = "",
) -> M.Analysis:
    """对某视频执行 1..target_layers 层拆解，返回 analysis 记录。"""
    analysis = M.Analysis(
        video_id=video.id,
        status="running",
        current_layer=0,
        meta={"model": model, "subtitle_len": len(subtitle_text), "media_brief_len": len(media_brief), "started_at": str(datetime.now(UTC))},
    )
    db.add(analysis)
    await db.flush()

    previous_summary = ""
    failed = 0
    for layer in range(1, target_layers + 1):
        ok, content, _raw = await _run_layer(
            db, analysis, video, subtitle_text, model, layer, previous_summary, media_brief
        )
        if ok:
            previous_summary = (
                previous_summary
                + f"\n[L{layer} 关键结论]\n"
                + json.dumps(content, ensure_ascii=False)[:1200]
            )
            await _persist_layer_artifacts(db, analysis, layer, content)
            if layer == 1:
                analysis.summary = {
                    k: content.get(k)
                    for k in ("one_liner", "topic", "hook_hypothesis", "target_audience")
                    if content.get(k) is not None
                }
        else:
            failed += 1
            previous_summary += f"\n[L{layer} 失败：{content.get('layer_error', '')}]"
        await db.commit()

    if failed:
        analysis.status = "partial" if failed < target_layers else "failed"
    else:
        analysis.status = "done"
    analysis.meta = {**(analysis.meta or {}), "finished_at": str(datetime.now(UTC)), "failed_layers": failed}
    await db.commit()
    return analysis


async def analysis_result(db: AsyncSession, analysis: M.Analysis) -> dict:
    """组装一次拆解的完整结果（供展示/接口返回）。"""
    layers = (
        (await db.execute(select(M.AnalysisLayer).where(M.AnalysisLayer.analysis_id == analysis.id).order_by(M.AnalysisLayer.layer)))
        .scalars()
        .all()
    )
    segments = (
        (await db.execute(select(M.Segment).where(M.Segment.analysis_id == analysis.id).order_by(M.Segment.seq)))
        .scalars()
        .all()
    )
    notes = (
        (await db.execute(select(M.AnalysisNote).where(M.AnalysisNote.analysis_id == analysis.id).order_by(M.AnalysisNote.created_at)))
        .scalars()
        .all()
    )
    elements = (
        (await db.execute(select(M.Element).where(M.Element.analysis_id == analysis.id)))
        .scalars()
        .all()
    )

    def layer_public(l: M.AnalysisLayer) -> dict:
        d = {
            "layer": l.layer,
            "step": l.step,
            "role_view": l.role_view,
            "content": l.content,
            "source_type": l.source_type,
            "confidence": l.confidence,
            "model": l.model,
            "prompt_version": l.prompt_version,
        }
        if l.layer == 3:
            d["segments"] = [
                {
                    "seq": s.seq,
                    "type": s.seg_type,
                    "title": s.title,
                    "start_ms": s.start_ms,
                    "end_ms": s.end_ms,
                    "hook_point": s.hook_point,
                    "payoff_point": s.payoff_point,
                    "emotion_level": s.emotion_curve,
                    "summary": s.summary,
                }
                for s in segments
            ]
        elif l.layer == 4:
            d["notes"] = [
                {
                    "note_type": n.note_type,
                    "start_ms": n.start_ms,
                    "end_ms": n.end_ms,
                    "role_view": n.role_view,
                    "content": n.content,
                    "confidence": n.confidence,
                }
                for n in notes
            ]
        elif l.layer == 5:
            d["elements"] = [
                {
                    "id": str(e.id),
                    "category": e.category,
                    "name": e.name,
                    "description": e.description,
                    "formula": e.formula,
                    "confidence": e.confidence,
                    "role_view": e.role_view,
                    "evidence": e.evidence,
                    "status": e.status,
                }
                for e in elements
            ]
        return d

    return {
        "id": str(analysis.id),
        "video_id": str(analysis.video_id),
        "status": analysis.status,
        "current_layer": analysis.current_layer,
        "summary": analysis.summary,
        "meta": analysis.meta,
        "layers": [layer_public(l) for l in layers],
    }
