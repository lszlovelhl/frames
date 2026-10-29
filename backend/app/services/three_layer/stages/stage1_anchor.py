"""Stage 1: L1 本片定调与事实锚定。
负责核心思想、内容走向、受众人群、定调校验与关键事实实体锚定。
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Awaitable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as M
from app.ai import chat as ai_chat
from app.services.llm_json import parse_json_loose
from app.services.three_layer import validate
from app.services.three_layer.prompts import LAYER_ARRAY_KEYS
from app.services.three_layer.stages.common import (
    Progress,
    _PipelineAbort,
    _run_layer,
)

logger = logging.getLogger(__name__)


async def run_stage1_anchor(
    db: AsyncSession,
    video: M.Video,
    *,
    analysis_id: str,
    title: str,
    base_meta: str,
    visual_ref: str,
    transcript: str,
    duration_ms: int,
    ctx: dict[str, Any],
    model: str,
    validator: validate.Validator,
    report: Progress,
    max_attempts: int = 2,
    max_tokens: int = 8192,
) -> tuple[M.ScriptScript, dict[str, Any], dict[str, Any], dict[str, Any]]:
    """执行 L1 本片定调并持久化 ScriptScript 记录。

    返回: (script, data1, q1, verdict_dict)
    """
    await report("L1", 52, "三层链路 · L1 本片定调…")
    ok, data1, q1 = await _run_layer(
        db,
        video=video,
        analysis_id=analysis_id,
        layer=1,
        user_text=(
            f"【视频元信息】\n{base_meta}\n\n"
            f"【画面参考（关键人物/事件线索，必须用于 key_facts）】\n{visual_ref}\n\n"
            f"【#03 逐句转写（含毫秒）】\n{transcript}"
        ),
        model=model,
        array_keys=LAYER_ARRAY_KEYS[1],
        max_tokens=max_tokens,
        max_attempts=max_attempts,
    )
    if not ok:
        raise _PipelineAbort(data1.get("layer_error", "L1 失败"))

    script = M.ScriptScript(
        video_id=video.id,
        title=title[:256] or None,
        platform=ctx["platform"][:32],
        category=(str(data1.get("category") or "")[:64] or None),
        duration_ms=duration_ms,
        core_idea=str(data1.get("core_idea") or "")[:2000] or "（缺失）",
        content_trend=str(data1.get("content_trend") or "")[:2000] or "（缺失）",
        target_audience=str(data1.get("target_audience") or "")[:2000] or "（缺失）",
        summary=str(data1.get("summary") or "")[:4000] or "（缺失）",
        status="draft",
    )
    verdict = validator.validate(
        "script_script",
        {
            "core_idea": script.core_idea,
            "content_trend": script.content_trend,
            "target_audience": script.target_audience,
            "summary": script.summary,
            "duration_ms": duration_ms,
        },
        label=f"script:{title[:20]}",
    )
    if not verdict.accepted:
        raise _PipelineAbort("L1 结构校验未通过：" + "；".join(verdict.hard))

    # ---- L1 核心思想事实锚定（编导自审①：核心思想误读，如直播八卦读成'揭秘产品'） ----
    # key_facts 实体须出现在 core_idea 中；未锚定即判解读漂移，触发定点重写
    kfs = data1.get("key_facts") or []
    entities: list[str] = []
    for _kf in kfs:
        if not isinstance(_kf, dict):
            continue
        _e = str(_kf.get("entity") or "").strip()
        if 1 < len(_e) <= 12 and not any(ch.isdigit() for ch in _e):
            entities.append(_e)
    entities = list(dict.fromkeys(entities))
    if entities:
        core_now = str(script.core_idea or "")
        _hit = [e for e in entities if e in core_now]
        if not _hit:
            q1.setdefault("warnings", []).append(
                f"核心思想未锚定关键事实实体（{entities[:5]}），判为解读漂移，触发定点重写"
            )
            try:
                _facts_txt = "\n".join(
                    f"- {f.get('entity')}：{f.get('relation')}（证据：{f.get('evidence')}）"
                    for f in kfs
                    if isinstance(f, dict)
                ) or "（无）"
                _rew = await ai_chat(
                    [
                        {
                            "role": "system",
                            "content": (
                                "你是爆款短视频拆解专家。给定视频关键事实清单，重写定调字段。"
                                "核心思想必须围绕清单中的真实人物与事件（讲清'谁和谁之间关于什么的事'），"
                                "禁止泛化成脱离事实的通用道理；内容走向/目标人群/摘要同步按事实修正。"
                                "只输出 JSON：{\"core_idea\":\"\",\"content_trend\":\"\","
                                "\"target_audience\":\"\",\"summary\":\"\"}"
                            ),
                        },
                        {
                            "role": "user",
                            "content": (
                                f"【关键事实清单】\n{_facts_txt}\n\n【原定调（疑似漂移）】\n"
                                f"core_idea: {script.core_idea}\ncontent_trend: {script.content_trend}\n"
                                f"target_audience: {script.target_audience}\nsummary: {script.summary}"
                            ),
                        },
                    ],
                    model=model,
                    max_tokens=2048,
                    json_mode=True,
                    timeout=120,
                    scene="tl1_anchor_rewrite",
                )
                _rd, _repaired = parse_json_loose((_rew or {}).get("reply") or "")
                if _repaired:
                    q1.setdefault("warnings", []).append("定调重写 JSON 经宽松修复后使用（建议核对字段完整性）")
                if isinstance(_rd, dict) and _rd.get("core_idea"):
                    script.core_idea = str(_rd["core_idea"])[:2000]
                    script.content_trend = str(_rd.get("content_trend") or script.content_trend)[:2000]
                    script.target_audience = str(_rd.get("target_audience") or script.target_audience)[:2000]
                    script.summary = str(_rd.get("summary") or script.summary)[:4000]
                    q1.setdefault("warnings", []).append(f"核心思想已按 key_facts 定点重写（原：{core_now[:40]}…）")
            except Exception as _exc:  # noqa: BLE001
                q1.setdefault("warnings", []).append(f"核心思想定点重写失败：{str(_exc)[:80]}")
    q1["key_facts"] = kfs[:6]

    db.add(script)
    await db.flush()
    await db.commit()

    # 落库验证（软告警）：确认 script_script 真的持久化
    try:
        _chk = await db.execute(text("SELECT 1 FROM script_script WHERE id=:sid"), {"sid": str(script.id)})
        if not _chk.scalar():
            logger.warning("落库验证告警：script.id=%s 不在数据库（commit 可能失败）", script.id)
    except Exception as _e:
        logger.warning("落库验证查询异常：%s", _e)

    return script, data1, q1, verdict.as_dict()
