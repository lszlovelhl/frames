"""三层流水线公共基础模块：
包含各阶段共享的常量、模式、工具函数、_run_layer 模型调用、事务清理与多模态帧理解。
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Sequence
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as M
from app.core.categories import normalize_category
from app.core.category_templates import render_template_hint
from app.services.analysis import load_active_prompt
from app.services.llm_json import complete_json, count_items
from app.services.zh import simplify_obj

from .. import emotion, validate
from ..few_shot import render_few_shots
from ..prompts import (
    LAYER_ARRAY_KEYS,
    LAYER_CODES,
    LAYER_LABELS,
    TEMPLATES,
    THREE_LAYER_CONTRACT,
)

logger = logging.getLogger(__name__)

Progress = Callable[[str, int, str], Awaitable[None]]

# L3 段数契约（与 prompts._TL3 一致）：flash 免费档实测段数 1~4 波动，需强校验 + 自动重试
L3_SEGMENT_MIN = 3
L3_SEGMENT_MAX = 14


def _placeholder_row() -> SimpleNamespace:
    """占位对象：lib_* 行 flush 前即需写 ref_element_source，先取占位 id，落库后回填。"""
    return SimpleNamespace(id=uuid4())


SCHEMAS: dict[int, dict[str, Any]] = {
    1: {
        "one_liner": "一句结论",
        "core_idea": "一句话中心思想",
        "content_trend": "从X走向Y",
        "target_audience": "目标人群与处境",
        "summary": "60~120字摘要",
        "topic_main": "选题主体",
        "pain_point": "观众痛点",
        "value_type": "实用|情绪|娱乐",
        "hook_type": "受控钩型",
        "hook_hypothesis": "钩子为什么在3秒内成立",
        "structure_hypothesis": [{"seq": 1, "title": "段名", "intent": "让观众发生什么变化"}],
        "narrative_order": "叙事顺序",
        "estimated_sentence_count": 38,
        "category": "内容赛道",
        "key_facts": [
            {"entity": "人物/组织/关键物", "relation": "人物间关系或事件", "evidence": "素材原话或画面证据"}
        ],
    },
    2: {
        "sample_interval_ms": 500,
        "intensity_series": [[0, 6.5], [500, 7.0]],
        "shape": "单峰|双峰|递进上升|波浪|骤升缓降|前高后低|平缓",
        "peak_position_ratio": 0.62,
        "baseline_intensity": 4.5,
        "rhythm_note": "节奏说明",
        "turn_points": [
            {"seq": 1, "type": "峰|谷|反转|悬念", "line_no": 3, "note": "为什么是关键点"}
        ],
    },
    3: {
        "segments": [
            {
                "seq": 1,
                "seg_type": "钩子|铺垫|冲突|转折|高潮|干货|CTA",
                "title": "段标题",
                "line_from": 1,
                "line_to": 2,
                "purpose": "为什么放在这个位置（≥30字）",
                "summary": "这一段做了什么",
                "hook_point": True,
                "payoff_point": False,
                "emotion_level": 6.5,
            }
        ]
    },
    4: {
        "sentences": [
            {
                "seq": 1,
                "line_no": 1,
                "quote": "逐字原话",
                "speaker": "口播|字幕|旁白",
                "sentence_function": "钩子|铺垫|冲突|转折|高潮|干货|CTA|过渡|收尾",
                "function_reason": "为什么有效、换题材怎么复用（≥30字）",
                "method_refs": ["hook.negate.misattribution"],
                "emotion_intensity": 6.5,
                "is_hook": True,
                "is_turn": False,
                "is_peak": False,
                "variants": ["变体1", "变体2"],
                "imagination": "想象/联想/构思发散（≥30字）",
                "evidence": [
                    {
                        "evidence_type": "transcript|ocr|frame|audio",
                        "content": "只写手法与可学点（≥20字）",
                        "quote": "引用的原话或画面文字",
                    }
                ],
            }
        ],
        "note": "句数自检说明（可空）",
    },
    5: {
        "topics": [
            {
                "code": "topic.pain.xxx",
                "name": "选题名",
                "topic_type": "痛点型|好奇型|利益型|身份型|反常识型",
                "audience": "人群",
                "pain_point": "痛点",
                "angle": "切入角度",
                "value_type": "实用|情绪|娱乐",
                "keywords": ["关键词"],
                "mechanism": "为什么有效（≥30字）",
                "variants": ["变体1", "变体2"],
                "imagination": "发散（≥30字）",
                "source_line_no": 1,
                "quote": "对应原话",
            }
        ],
        "hooks": [
            {
                "code": "hook.negate.warning",
                "name": "钩子名",
                "hook_type": "受控钩型",
                "position": "前3秒|片中|结尾",
                "sentence_pattern": "句式模板[变量]",
                "variables": ["变量"],
                "expected_effect": "预期效果",
                "mechanism": "≥30字",
                "variants": ["变体1", "变体2"],
                "imagination": "≥30字",
                "source_line_no": 1,
                "quote": "对应原话",
            }
        ],
        "copywriting": [
            {
                "code": "copy.enumerate.detail",
                "name": "文案名",
                "copy_type": "口播|字幕|标题|CTA|封面文案",
                "sentence_pattern": "句式模板",
                "rhetoric": "设问|排比|对比|夸张|比喻|反问|递进|白描|数字锚定",
                "example_text": "原片实例",
                "mechanism": "≥30字",
                "variants": ["变体1", "变体2"],
                "imagination": "≥30字",
                "source_line_no": 1,
                "quote": "对应原话",
            }
        ],
        "quotes": [
            {
                "code": "quote.contrast.core",
                "text": "金句原句",
                "structure": "句式结构",
                "rewrite_template": "改写模板",
                "applicable_scene": "适用场景",
                "mechanism": "≥30字",
                "variants": ["变体1", "变体2"],
                "imagination": "≥30字",
                "source_line_no": 1,
                "quote": "对应原话",
            }
        ],
        "methods": [
            {
                "code": "method.struct.conclusion_first",
                "name": "手法名",
                "category": "叙事|结构|修辞|视听|节奏|互动|运营",
                "controlled_tag": "可空",
                "abstraction_level": "句法级|段落级|全片级",
                "mechanism": "为什么有效（≥30字）",
                "usage_steps": "先…→再…→然后…（≥30字）",
                "counter_example": "什么情况会失效",
                "variants": ["变体1", "变体2"],
                "imagination": "≥30字",
                "source_line_no": 1,
                "quote": "对应原话",
            }
        ],
    },
    6: {
        "combo": {
            "code": "emo.relatable.escalate",
            "name": "组合模板名",
            "intent": "涨粉|带货|种草|引流|科普|情绪共鸣",
            "core_idea_alignment": "手法如何围绕中心思想组合（≥30字）",
            "content_trend": "内容走向",
            "sequence_desc": "槽位序列的组合逻辑",
            "emotion_shape": "单峰|双峰|递进上升|波浪|骤升缓降|前高后低|平缓",
            "mechanism": "为什么这个顺序有效（≥30字）",
            "variants": ["变体1", "变体2"],
            "imagination": "≥30字",
        },
        "slots": [
            {
                "seq": 1,
                "segment_seq": 1,
                "slot_role": "固定|可替换",
                "role_reason": "判定理由",
                "method_code": "method.xxx.yyy",
                "expected_function": "让观众发生什么（≥20字）",
                "position_ratio_start": 0.0,
                "position_ratio_end": 0.25,
                "duration_ratio": 0.25,
                "swap_alternatives": ["method.aaa.bbb"],
            }
        ],
        "emotion_curve": [[0.0, 6.5], [0.25, 5.0]],
    },
}


# ---------------- 文本与格式化工具 ----------------

def _fmt_transcript(sentences: Sequence[dict[str, Any]], *, with_ms: bool = True) -> str:
    lines = []
    for s in sentences:
        head = f"#{int(s['seq']):02d}"
        if with_ms:
            head += f" [{int(s['start_ms'])}-{int(s['end_ms'])}ms]"
        src = s.get("src")
        if src:
            head += f"（{src}）"
        lines.append(f"{head} {s['text']}")
    return "\n".join(lines)


def _looks_lyric(text: str) -> bool:
    """启发式：英文占比 >60% 的句子视为 BGM 歌词（ASR 把歌转写进来了）。"""
    if not text:
        return False
    latin = sum(1 for ch in text if ("a" <= ch.lower() <= "z") or ch in " ,'-.!?")
    return latin / max(len(text), 1) > 0.6


def _flag_asr_dubious(text: str) -> bool:
    """启发式：ASR 存疑句标记（编导审稿：素材解读失真——乱码句被硬编创作意图）。"""
    if not text:
        return False
    t = str(text)
    for m in re.finditer(r"([\u4e00-\u9fa5]{2,4})\1", t):
        return True
    fillers = re.findall(r"这边|就是说|你知道吗|就跟你讲|我跟你说|然后呢", t)
    if len(fillers) >= 2 and len(t) <= 60:
        return True
    if not re.search(r"[，。！？；、,.!?;]", t) and len(t) > 35:
        return True
    return False


_STRUCT_WORDS = (
    "节奏", "峰值", "谷底", "曲线", "差异化", "硬约束", "承接上文",
    "观众此刻", "位于曲线", "相邻段", "把观众从", "推往", "情绪中",
    "情绪", "较快", "较慢", "适中", "峰值位置", "本段使用", "来展示",
    "来引发", "来吸引", "使用口播", "3D动画", "和", "的置", "本段",
    "来获得", "以此", "从而", "让观众", "这种", "观众处于",
    "本段采用", "画面细节丰富", "节奏快速", "节奏稍慢", "手法",
    "细节丰富", "通过描述", "来增加", "增加故事", "的冲突性",
    "快速", "稍慢", "位于", "位置", "观众", "认知", "注意力",
    "补充", "口播", "画面字幕", "呈现了", "内容与",
    "适中", "下一段", "形成对比", "与下一段",
    "好奇", "期待", "震撼", "思考", "惊讶", "惊喜", "幽默", "荒诞",
    "情绪推往", "推往", "情绪中",
)


def _is_cliche_pair(a: str, b: str, best: str) -> bool:
    quotes = lambda t: set(re.findall(r"[“\"'『「]([^”\"'』」]{4,})[”\"'』」]", t))
    qa, qb = quotes(a), quotes(b)
    if qa and qb:
        overlap = len(qa & qb) / min(len(qa), len(qb))
        return overlap >= 0.5
    rest = best
    for w in _STRUCT_WORDS:
        rest = rest.replace(w, "")
    return len(rest) >= 8


def _detect_annotation_cliches(full_script: str) -> list[str]:
    hits: list[str] = []
    for pat, label, thr in (
        (r"为后续[^，。]{0,12}(?:剧情|故事|情节|内容)[^，。]{0,12}(?:做铺垫|埋下伏笔|做过渡|奠定基础)",
         "为后续剧情/故事…做铺垫/伏笔/过渡", 1),
        (r"为后续情节[^，。]{0,8}做铺垫", "为后续情节…做铺垫", 1),
        (r"为后续情节[^，。]{0,8}做过渡", "为后续情节…做过渡", 1),
        (r"(?:实现|自然|完成|从而|以此)(?:过渡|转折)", "实现/自然…过渡/转折", 1),
        (r"对话[^，。]{0,10}(?:过渡|转向)", "对话…过渡/转向", 1),
        (r"(?:剧情|故事|情节|内容)[^，。]{0,10}(?:曲折|推进|发展|高潮)", "剧情…曲折/推进/高潮", 1),
        (r"(?:再次|进一步)?制造(?:冲突|悬念|紧张感)", "制造冲突/悬念/紧张感", 1),
        (r"(?:留下)?深刻印象", "留下深刻印象", 1),
        (r"增加(?:代入感|真实感|层次感)", "增加代入感/真实感/层次感", 1),
        (r"营造[^，。]{0,8}(?:氛围|气氛|感觉)", "营造…氛围/气氛", 3),
        (r"展现[^，。]{0,12}(?:温馨|美好|活力|校园生活|日常)", "展现…温馨/美好/活力", 3),
        (r"传递出[^，。]{0,8}(?:温馨|美好|快乐|活力|情感)", "传递出…情感", 3),
        (r"为观众提供信息", "为观众提供信息", 3),
        ("增加故事的荒诞性和紧张感", "增加故事的荒诞性和紧张感", 3),
        ("吸引观众的注意力", "吸引观众的注意力", 3),
        ("激发观众的好奇心", "激发观众的好奇心", 3),
    ):
        _target = re.sub(r"〔句\d+[^\n]*语音不清[^\n]*", "", full_script)
        n = len(re.findall(pat, _target))
        if n >= thr:
            hits.append(f"逐句套话 '{label}' ×{n}（跨句模板填充，需改写成引用原话字词）")
    if "差异化硬约束" in full_script:
        hits.append("提示词术语泄漏：'差异化硬约束'出现在成稿（内部字段名禁止进入产物）")
    n_wave = len(re.findall(r"波浪形", full_script))
    if n_wave >= 2:
        hits.append(f"节奏描述模板化：'波浪形' ×{n_wave}（节奏应点名峰谷对应的具体段/事件）")
    if "口播结束后的画面段" in full_script:
        hits.append("补段标题残留：'口播结束后的画面段'是 harness 默认说明，应写内容标题（如'美妆技巧展示'）")
    seg_blocks = re.split(r"### 段\d+", full_script)
    if len(seg_blocks) < 3:
        return hits[:8]
    annotations: list[str] = []
    for block in seg_blocks[1:]:
        m = re.search(
            r"(?:创作注解|创作注解：)(.*?)(?=逐句|- 〔句\d|\n### |$)", block, re.S)
        if m:
            annotations.append(re.sub(r"\s+", "", m.group(1)))
    n = len(annotations)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = annotations[i], annotations[j]
            if not a or not b:
                continue
            best = ""
            for k in range(min(len(a), len(b)) - 11):
                sub = a[k:k + 12]
                if sub in b:
                    cur = sub
                    while k + len(cur) < len(a) and cur + a[k + len(cur)] in b:
                        cur += a[k + len(cur)]
                    if len(cur) > len(best):
                        best = cur
            if len(best) >= 12 and _is_cliche_pair(annotations[i], annotations[j], best):
                hits.append(f"段{i + 1} 与 段{j + 1} 创作注解雷同（共用 '{best[:24]}…'）")
                break
    for block in seg_blocks[1:]:
        _sm = re.search(r"补充[：:]\s*([^\n]{8,})", block)
        if _sm:
            _sup = re.sub(r"\s+", "", _sm.group(1))
            _am = re.search(
                r"(?:创作注解|创作注解：)(.*?)(?=补充[：:]|逐句|- 〔句\d|\n### |$)", block, re.S)
            if _am and _sup[:10] in re.sub(r"\s+", "", _am.group(1)):
                hits.append(f"'补充：'与创作注解正文重复（'{_sup[:20]}…'是正文子句，应删除或改写）")
    _cut_raw = [
        re.sub(r"\s+", "", _m.group(1)).strip().rstrip("。")
        for _m in re.finditer(r"【剪辑】([^；\n}]{4,40})", full_script)
    ]
    if _cut_raw:
        from collections import Counter
        for _c, _n in Counter(_cut_raw).most_common(2):
            if _n >= 3:
                hits.append(f"剪辑建议雷同：'{_c}' 出现 {_n} 次（编导要求每条剪辑结合本句原话/画面，全片最多 1 次）")
    return hits[:8]


def _fmt_energy(energy: Sequence[dict[str, Any]]) -> str:
    if not energy:
        return "（无音频能量采样）"
    return " ".join(f"{int(e['t_ms'])}:{float(e['energy']):.2f}" for e in energy)


def normalize_code(raw: Any) -> str:
    parts = [p for p in re.split(r"[.\-/\s]+", str(raw or "").strip().lower()) if p]
    parts = [re.sub(r"[^a-z0-9_]", "_", p).strip("_") for p in parts]
    parts = [p for p in parts if p]
    if len(parts) < 2:
        return ""
    if len(parts) == 2:
        parts = [parts[0], "misc", parts[1]]
    elif len(parts) > 3:
        parts = [parts[0], parts[1], "_".join(parts[2:])]
    code = ".".join(parts[:3])
    return code if validate.is_code(code) else ""


def _extract_line_no(raw: Any) -> int | None:
    if raw is None:
        return None
    m = re.search(r"\d+", str(raw))
    return int(m.group(0)) if m else None


def _line_to_sentence(sentences: Sequence[dict[str, Any]], line_no: Any) -> dict[str, Any] | None:
    seq = None
    try:
        seq = int(line_no)
    except (TypeError, ValueError):
        seq = _extract_line_no(line_no)
    if seq is None or not sentences:
        return None
    seq = max(1, min(seq, int(sentences[-1]["seq"])))
    for s in sentences:
        if int(s["seq"]) == seq:
            return s
    return None


def _next_sentence_after(
    sentences: Sequence[dict[str, Any]], prev: dict[str, Any] | None
) -> dict[str, Any] | None:
    if not sentences:
        return None
    if prev is None:
        return sentences[0]
    for s in sentences:
        if int(s["seq"]) > int(prev["seq"]):
            return s
    return None


def _sentence_at(sentences: Sequence[dict[str, Any]], t_ms: int) -> dict[str, Any] | None:
    if not sentences:
        return None
    for s in sentences:
        if int(s["start_ms"]) <= t_ms < int(s["end_ms"]):
            return s
    return min(sentences, key=lambda s: abs((int(s["start_ms"]) + int(s["end_ms"])) / 2 - t_ms))


def _clamp_intensity(v: Any) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return round(max(0.0, min(10.0, f)), 1)


def _derive_turn_points(
    sentences: Sequence[dict[str, Any]],
    grid: Sequence[int],
    values: Sequence[float],
    model_turns: Sequence[dict[str, Any]] = (),
) -> list[dict[str, Any]]:
    info = emotion.analysis(values, grid)
    picked: list[dict[str, Any]] = []

    for i in sorted(info["peaks"], key=lambda i: -values[i])[:2]:
        picked.append({"turn_type": "峰", "t_ms": grid[i], "intensity": values[i]})
    for i in sorted(info["valleys"], key=lambda i: values[i])[:2]:
        picked.append({"turn_type": "谷", "t_ms": grid[i], "intensity": values[i]})
    idx = emotion.direction_change_index(values)
    if idx is not None:
        picked.append({"turn_type": "反转", "t_ms": grid[idx], "intensity": values[idx]})
    idx = emotion.suspension_index(values)
    if idx is not None:
        picked.append({"turn_type": "悬念", "t_ms": grid[idx], "intensity": values[idx]})

    pool: dict[str, list[dict[str, Any]]] = {}
    for t in model_turns or []:
        tt = str(t.get("type") or t.get("turn_type") or "").strip()
        if tt in ("峰", "谷", "反转", "悬念"):
            pool.setdefault(tt, []).append(t)

    out: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for point in picked:
        note = ""
        sentence = None
        candidates = pool.get(point["turn_type"]) or []
        if candidates:
            item = candidates.pop(0)
            note = str(item.get("note") or "")
            sentence = _line_to_sentence(sentences, item.get("line_no"))
        if sentence is None:
            sentence = _sentence_at(sentences, point["t_ms"])
        key = (point["turn_type"], int(point["t_ms"]))
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "turn_type": point["turn_type"],
                "t_ms": int(point["t_ms"]),
                "intensity": _clamp_intensity(point["intensity"]),
                "sentence": sentence,
                "note": note or f"曲线在该点出现{point['turn_type']}（强度 {_clamp_intensity(point['intensity'])}）"
                + (f"，对应台词「{sentence['text'][:20]}」" if sentence else ""),
            }
        )
    out.sort(key=lambda p: p["t_ms"])
    return [dict(p, seq=i + 1) for i, p in enumerate(out)][:8]


# ---------------- 单层 LLM 调用与重试 ----------------


def _exc_chain(exc: BaseException) -> str:
    """把异常链拼成一行可读字符串；str(exc) 为空时只留类名，避免 failed_reason 出现空白。"""
    s = str(exc)
    parts = [f"{type(exc).__name__}: {s}" if s else type(exc).__name__]
    cause = exc.__cause__ or exc.__context__
    while cause:
        cs = str(cause)
        parts.append(f"  ← {type(cause).__name__}" + (f": {cs}" if cs else ""))
        cause = cause.__cause__ or cause.__context__
    return "\n".join(parts)


async def _run_layer(
    db: AsyncSession,
    *,
    video: M.Video,
    analysis_id: str,
    layer: int,
    user_text: str,
    model: str,
    array_keys: Sequence[str],
    max_tokens: int = 16384,
    max_attempts: int = 4,
) -> tuple[bool, dict[str, Any], dict[str, Any]]:
    code = LAYER_CODES[layer]
    template = await load_active_prompt(db, code)
    if template is None:
        seed = TEMPLATES.get(code, {})
        content = seed.get("content", "")
        logger.warning("提示词 %s 未落库，使用代码内置默认版本", code)
        system = content + THREE_LAYER_CONTRACT
        version = 0
    else:
        system = template.content + THREE_LAYER_CONTRACT
        version = template.version

    if layer == 5:
        cat = normalize_category(getattr(video, "category_guess", None))
        extra = [render_few_shots(cat, layer=5)]
        tpl_hint = render_template_hint(cat)
        if tpl_hint:
            extra.append(tpl_hint)
        extra = [e for e in extra if e]
        if extra:
            system = system + "\n\n" + "\n\n".join(extra)

    schema_hint = json.dumps(SCHEMAS[layer], ensure_ascii=False, indent=1)
    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": f"{user_text}\n\n必须严格输出 JSON 对象，字段结构如下（不得增删字段名，取不到的时间写 0）：\n{schema_hint}",
        },
    ]

    logger.info("三层链路 L%s 开始：system=%d字符, user=%d字符, max_tokens=%d", layer, len(system), len(user_text), max_tokens)

    last_exc = ""
    attempts = (0.2, 0.6, 0.2, 0.6)[: max(1, max_attempts)]
    for attempt, temperature in enumerate(attempts):
        if attempt:
            await asyncio.sleep(min(3 * attempt * attempt, 20))
        try:
            data, info = await complete_json(
                messages,
                array_keys=list(array_keys),
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=300,
                scene="three_layer",
                ref_type="analysis",
                ref_id=str(analysis_id),
                max_rounds=3,
            )
            if data:
                data = simplify_obj(data)
                quality = {
                    "items": count_items(data, list(array_keys)) if array_keys else 1,
                    "truncated": bool(info.get("truncated")),
                    "repaired": bool(info.get("repaired")),
                    "rounds": info.get("rounds"),
                    "chars": len(info.get("raw") or ""),
                    "prompt_version": version,
                }
                return True, data, quality
            last_exc = f"第 {attempt + 1} 次返回空内容"
        except Exception as exc:  # noqa: BLE001
            last_exc = _exc_chain(exc)
            logger.warning("三层链路 L%s 调用失败（第 %s 次）：\n%s", layer, attempt + 1, last_exc)
    return False, {"layer_error": last_exc}, {"items": 0, "prompt_version": version}


# ---------------- 画面理解 ----------------

async def _understand_video_frames(
    shots: list[dict[str, Any]],
    scenes: list[dict[str, Any]] | None = None,
    model: str = "vision",
) -> str:
    if scenes and len(scenes) > 1:
        return await _understand_video_frames_ip(shots, scenes, model)
    
    import base64
    from app.ai import chat as _ai_chat

    if not shots:
        return ""

    frames_per_segment = 5
    segments = []
    for i in range(0, len(shots), frames_per_segment):
        seg_shots = shots[i:i + frames_per_segment]
        if seg_shots:
            segments.append(seg_shots)

    segment_summaries = []
    for i, seg_shots in enumerate(segments):
        content = [{"type": "text", "text": f"这是视频第 {i+1} 段的关键帧，简单描述画面内容、人物、场景、动作、字幕文字"}]
        for s in seg_shots:
            path = s.get("path", "")
            if not path or not os.path.exists(path):
                continue
            try:
                with open(path, "rb") as fh:
                    img_b64 = base64.b64encode(fh.read()).decode()
                content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}})
            except Exception:
                continue

        if len(content) <= 1:
            segment_summaries.append(f"第 {i+1} 段：无可用帧图")
            continue

        try:
            res = await _ai_chat(
                [{"role": "user", "content": content}],
                model=model,
                max_tokens=500,
                timeout=60,
                scene="video_understand",
            )
            summary = res.get("reply", "").strip()
            t_start = seg_shots[0].get("start_ms", 0) / 1000
            t_end = seg_shots[-1].get("end_ms", 0) / 1000
            segment_summaries.append(f"第 {i+1} 段（{t_start:.1f}s-{t_end:.1f}s）：{summary}")
        except Exception as e:
            logger.warning("视频理解第 %d 段失败：%s", i+1, str(e)[:100])
            segment_summaries.append(f"第 {i+1} 段：理解失败")

    return "\n".join(segment_summaries)


async def _understand_video_frames_ip(
    shots: list[dict[str, Any]],
    scenes: list[dict[str, Any]],
    model: str = "vision",
) -> str:
    import base64
    from app.ai import chat as _ai_chat

    scene_shots = [[] for _ in scenes]
    for s in shots:
        s_start = s.get("start_ms", 0)
        for i, sc in enumerate(scenes):
            sc_start = sc.get("start_ms", 0)
            sc_end = sc.get("end_ms", 0)
            if sc_start <= s_start < sc_end:
                scene_shots[i].append(s)
                break

    scene_summaries = []
    prev_summary = ""

    for i, sc in enumerate(scenes):
        sc_shots = scene_shots[i]
        if not sc_shots:
            continue

        t_start = sc.get("start_ms", 0) / 1000
        t_end = sc.get("end_ms", 0) / 1000
        is_i_frame = (i == 0)

        if is_i_frame:
            prompt = f"这是视频第 {i+1} 个场景（{t_start:.1f}s-{t_end:.1f}s）的关键帧，完整描述画面内容、人物、场景、动作、字幕文字"
        else:
            prompt = f"这是视频第 {i+1} 个场景（{t_start:.1f}s-{t_end:.1f}s）的关键帧。\n上一个场景的描述：{prev_summary}\n请只描述这个场景与上一个场景相比有什么变化（人物变化、场景变化、动作变化、新出现的字幕等）"

        content = [{"type": "text", "text": prompt}]
        for s in sc_shots:
            path = s.get("path", "")
            if not path or not os.path.exists(path):
                continue
            try:
                with open(path, "rb") as fh:
                    img_b64 = base64.b64encode(fh.read()).decode()
                content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}})
            except Exception:
                continue

        if len(content) <= 1:
            scene_summaries.append(f"场景 {i+1}（{t_start:.1f}s-{t_end:.1f}s）：无可用帧图")
            continue

        try:
            res = await _ai_chat(
                [{"role": "user", "content": content}],
                model=model,
                max_tokens=500 if is_i_frame else 300,
                timeout=60,
                scene="video_understand",
            )
            summary = res.get("reply", "").strip()
            frame_type = "I帧（完整画面）" if is_i_frame else "P帧（变化）"
            scene_summaries.append(f"场景 {i+1}（{t_start:.1f}s-{t_end:.1f}s）[{frame_type}]：{summary}")
            prev_summary = summary
        except Exception as e:
            logger.warning("视频理解场景 %d 失败：%s", i+1, str(e)[:100])
            scene_summaries.append(f"场景 {i+1}：理解失败")

    return "\n".join(scene_summaries)


# ---------------- 异常中断与事务清理 ----------------

class _PipelineAbort(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


async def purge_three_layer(db: AsyncSession, video_id) -> None:
    """清掉本片的三层产物（脚本层整片重写 + 本片溯源），积木库行按 code 覆盖不删除。"""
    await db.execute(delete(M.RefElementSource).where(M.RefElementSource.source_video_id == video_id))
    script_ids = (
        await db.execute(select(M.ScriptScript.id).where(M.ScriptScript.video_id == video_id))
    ).scalars().all()
    if script_ids:
        sent_ids = (
            await db.execute(select(M.ScriptSentence.id).where(M.ScriptSentence.script_id.in_(script_ids)))
        ).scalars().all()
        if sent_ids:
            await db.execute(delete(M.ScriptEvidence).where(M.ScriptEvidence.sentence_id.in_(sent_ids)))
            await db.execute(delete(M.ScriptTurnPoint).where(M.ScriptTurnPoint.sentence_id.in_(sent_ids)))
        await db.execute(delete(M.ScriptEmotionCurve).where(M.ScriptEmotionCurve.script_id.in_(script_ids)))
        await db.execute(delete(M.ScriptSentence).where(M.ScriptSentence.script_id.in_(script_ids)))
        await db.execute(delete(M.ScriptSegment).where(M.ScriptSegment.script_id.in_(script_ids)))
        await db.execute(delete(M.ScriptScript).where(M.ScriptScript.id.in_(script_ids)))
    await db.flush()
    db.expunge_all()


async def _abort_cleanup(db: AsyncSession, video_id) -> None:
    try:
        await db.rollback()
        db.expunge_all()
        await purge_three_layer(db, video_id)
        await db.commit()
        db.expunge_all()
    except Exception:  # noqa: BLE001
        logger.warning("三层链路失败后清理中间产物失败：video=%s", video_id, exc_info=True)
        try:
            await db.rollback()
            db.expunge_all()
        except Exception:  # noqa: BLE001
            pass
