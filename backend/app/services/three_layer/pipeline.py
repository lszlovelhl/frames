"""三层分库拆解链路编排：L1 → L6 → raw_*/script_*/lib_* 落库 + 第 7.2 节校验。

与旧五层链路并存：本模块只写三层新表（``raw_*`` / ``script_*`` / ``lib_*`` / ``ref_*``），
不读不写 ``analyses`` / ``analysis_layers`` / ``segments`` / ``analysis_notes`` / ``elements`` 等旧表，
保证"严禁删除或清空既有数据与旧表"的约束成立。
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Sequence
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as M
from app.services.analysis import load_active_prompt
from app.services.llm_json import complete_json, count_items
from app.services.zh import simplify_obj

from . import emotion, validate
from .prompts import (
    LAYER_ARRAY_KEYS,
    LAYER_CODES,
    LAYER_LABELS,
    TEMPLATES,
    THREE_LAYER_CONTRACT,
)
from .raw import load_raw_context, persist_raw_layer

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


# ---------------- 工具 ----------------

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
    """启发式：ASR 存疑句标记（编导审稿：素材解读失真——乱码句被硬编创作意图）。

    直播/方言/口误场景下 ASR 常见乱码：句内连续重复片段（"财务财务""我会是我会"）、
    填充口头禅密度高（"这边…这边"）、吞字断句异常。这类句子在拆解时
    禁止编造具体语义解读，只能标注"语音不清"或"直播杂音"。
    """
    if not text:
        return False
    t = str(text)
    # 特征1：句内连续重复片段（≥2 字重复出现，如"财务财务""我会是我会"）
    for m in re.finditer(r"([\u4e00-\u9fa5]{2,4})\1", t):
        return True
    # 特征2：填充口头禅 ≥2 个且句长 ≤60（"这边…这边""就跟你讲…就跟你讲"）
    fillers = re.findall(r"这边|就是说|你知道吗|就跟你讲|我跟你说|然后呢", t)
    if len(fillers) >= 2 and len(t) <= 60:
        return True
    # 特征3：超长无标点句（>35 字无任何标点，直播快语速 ASR 难断句）
    if not re.search(r"[，。！？；、,.!?;]", t) and len(t) > 35:
        return True
    return False


def _detect_annotation_cliches(full_script: str) -> list[str]:
    """跨段创作注解雷同检测 + 逐句套话检测。

    编导审稿视角：'通过XX吸引注意力''为后续情节做铺垫'等句式可套到任何片段，
    多段出现即视为套话。五要素结构词（节奏/峰谷/曲线等）不算雷同。
    返回可读告警列表。
    """
    hits: list[str] = []
    # --- 逐句套话：模板句计数（措辞变体合并统计） ---
    for pat, label, thr in (
        # 铺垫/伏笔/过渡家族变体（编导自审发现："为后续剧情…做铺垫"漏检）
        (r"为后续[^，。]{0,12}(?:剧情|故事|情节|内容)[^，。]{0,12}(?:做铺垫|埋下伏笔|做过渡|奠定基础)",
         "为后续剧情/故事…做铺垫/伏笔/过渡", 1),
        (r"为后续情节[^，。]{0,8}做铺垫", "为后续情节…做铺垫", 1),
        (r"为后续情节[^，。]{0,8}做过渡", "为后续情节…做过渡", 1),
        # 编导 v4 审稿点名变体：功能名填空/万能句（逐句【功能】区）——出现即报
        (r"(?:实现|自然|完成|从而|以此)(?:过渡|转折)", "实现/自然…过渡/转折", 1),
        (r"对话[^，。]{0,10}(?:过渡|转向)", "对话…过渡/转向", 1),
        (r"(?:剧情|故事|情节|内容)[^，。]{0,10}(?:曲折|推进|发展|高潮)", "剧情…曲折/推进/高潮", 1),
        (r"(?:再次|进一步)?制造(?:冲突|悬念|紧张感)", "制造冲突/悬念/紧张感", 1),
        (r"(?:留下)?深刻印象", "留下深刻印象", 1),
        (r"增加(?:代入感|真实感|层次感)", "增加代入感/真实感/层次感", 1),
        # 万能空话库（BGM/氛围类"营造氛围/展现温馨"）
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
    # --- 提示词术语泄漏（内部字段名进入成稿，全文检测） ---
    if "差异化硬约束" in full_script:
        hits.append("提示词术语泄漏：'差异化硬约束'出现在成稿（内部字段名禁止进入产物）")
    # --- 节奏描述模板化（"波浪形"式空泛，要点名峰谷对应具体段/事件） ---
    n_wave = len(re.findall(r"波浪形", full_script))
    if n_wave >= 2:
        hits.append(f"节奏描述模板化：'波浪形' ×{n_wave}（节奏应点名峰谷对应的具体段/事件）")
    # --- 补段默认标题残留（harness 补段 title=None 时 L4.5 未重写） ---
    if "口播结束后的画面段" in full_script:
        hits.append("补段标题残留：'口播结束后的画面段'是 harness 默认说明，应写内容标题（如'美妆技巧展示'）")
    # --- 段落创作注解雷同 ---
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
    # --- "补充："与注解正文重复（重写产物废话） ---
    for block in seg_blocks[1:]:
        _sm = re.search(r"补充[：:]\s*([^\n]{8,})", block)
        if _sm:
            _sup = re.sub(r"\s+", "", _sm.group(1))
            _am = re.search(
                r"(?:创作注解|创作注解：)(.*?)(?=补充[：:]|逐句|- 〔句\d|\n### |$)", block, re.S)
            if _am and _sup[:10] in re.sub(r"\s+", "", _am.group(1)):
                hits.append(f"'补充：'与创作注解正文重复（'{_sup[:20]}…'是正文子句，应删除或改写）")
    # --- 剪辑建议雷同：同一条【剪辑】动作在全片出现 ≥3 次 → 套话新皮 ---
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


_STRUCT_WORDS = ("节奏", "峰值", "谷底", "曲线", "差异化", "硬约束", "承接上文",
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
                 "情绪推往", "推往", "情绪中")


def _is_cliche_pair(a: str, b: str, best: str) -> bool:
    """判定两段注解是否真雷同：核心看是否点名了【不同的具体证据】。

    两段都引用了具体原话/字幕（引号内容）且证据不同 → 手法各异，连接词共享不算雷同；
    只要有一段没点名任何具体证据，且公共子串去模板词后仍有实质内容 → 判雷同。
    """
    quotes = lambda t: set(re.findall(r"[“\"'『「]([^”\"'』」]{4,})[”\"'』」]", t))
    qa, qb = quotes(a), quotes(b)
    if qa and qb:
        overlap = len(qa & qb) / min(len(qa), len(qb))
        return overlap >= 0.5  # 引用了同一批证据 → 雷同；证据不同 → 不雷同
    rest = best
    for w in _STRUCT_WORDS:
        rest = rest.replace(w, "")
    return len(rest) >= 8


def _fmt_energy(energy: Sequence[dict[str, Any]]) -> str:
    if not energy:
        return "（无音频能量采样）"
    return " ".join(f"{int(e['t_ms'])}:{float(e['energy']):.2f}" for e in energy)


def normalize_code(raw: Any) -> str:
    """把模型输出的 ``code`` 归一化为三段式 ``{类}.{小类}.{短名}``。

    模型常见两种偏差：①写成四段以上（``topic.pain.game.recommendation``）；
    ②只用两段（``hook.question``）。四段以上时把第 3 段起合并为短名，两段时补 ``misc``，
    这样既不丢语义也不因格式问题静默丢弃整条积木。无法归一化时返回空串。
    """
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
    """从任意值中尽力提取句子序号：'5'、'第5句'、'line 5'、'5-6' → 5。失败返回 None。"""
    if raw is None:
        return None
    m = re.search(r"\d+", str(raw))
    return int(m.group(0)) if m else None


def _line_to_sentence(sentences: Sequence[dict[str, Any]], line_no: Any) -> dict[str, Any] | None:
    """句子定位：优先精确 seq；模型给非法/越界行号时 clamp 到边界句，避免整段丢失。"""
    seq = None
    try:
        seq = int(line_no)
    except (TypeError, ValueError):
        seq = _extract_line_no(line_no)
    if seq is None:
        return None
    if not sentences:
        return None
    seq = max(1, min(seq, int(sentences[-1]["seq"])))
    for s in sentences:
        if int(s["seq"]) == seq:
            return s
    return None


def _next_sentence_after(
    sentences: Sequence[dict[str, Any]], prev: dict[str, Any] | None
) -> dict[str, Any] | None:
    """harness 段落时间锚定：返回 prev 之后的首个句子（prev=None 时返回首句）。

    模型给非法 line 号时用它在'前一段末尾之后'继续，而不是兜底回首句——
    兜底首句会让段落 start_ms=0 与段 1 重叠，污染 L6 组合模板槽位覆盖。
    """
    if not sentences:
        return None
    if prev is None:
        return sentences[0]
    for s in sentences:
        if int(s["seq"]) > int(prev["seq"]):
            return s
    return None


R12_RETRY_SYSTEM = (
    "你是短视频拆解系统的元素质量修复器。给定一个已提取的元素和校验反馈，"
    "只修复【发散想象（imagination）】与【变体（variants）】两个字段，其他字段一律保持原样。\n"
    "要求：\n"
    "- imagination：用中文自然语言描述该元素可以如何迁移到其他场景/品类/人群/平台，"
    "必须 ≥30 字（宁可 35 字也不要 28 字），落到具体可执行的场景细节，禁止空泛套话。\n"
    "- variants：2~3 条发散变体，每条是独立不重复的方向（互相相似度必须 <0.8），"
    "每条给出具体可执行的改写方向，不得只是换词。\n"
    "只输出 JSON：{\"imagination\": \"...\", \"variants\": [{\"text\": \"...\"}, ...]}"
)


async def _retry_r12_fields(
    item: dict[str, Any], feedback: list[str], *, model: str
) -> dict[str, Any] | None:
    """harness 定点重试：R12 违规元素只重跑 imagination+variants，返回修复字段（不达标则 None）。"""
    payload = {
        k: item.get(k)
        for k in ("code", "name", "text", "mechanism", "variants", "imagination")
        if item.get(k) is not None
    }
    user = (
        "【元素当前内容】\n" + json.dumps(payload, ensure_ascii=False)
        + "\n\n【校验反馈】\n" + "\n".join(f"- {f}" for f in feedback)
        + "\n\n按要求修复后，只输出 imagination 与 variants 两个字段的 JSON。"
    )
    try:
        data, _info = await complete_json(
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
    validator,
    table: str,
    label: str,
    item: dict[str, Any],
    values: dict[str, Any],
    v,
    refs: list[dict],
) -> tuple[bool, Any, dict[str, Any]]:
    """R12 软违规（imagination<30 / 变体重复）→ 定点重试 → 更新 values 并重新校验。

    返回 (最终 accepted, 最终 verdict, 最终 values)；未违规或重试失败时原判定不变。
    这是 harness 对确定性约束的接管：字数/重复检查+定点重试归代码层，模型只负责重写内容。
    """
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


# ---------------- 定点深度化（R15：内容单薄 → 单元素深度重写） ----------------

DEEP_SYSTEM = (
    "你是短视频拆解系统的深度化器。给定一个已提取的元素及其来源上下文，把【机制（mechanism）】"
    "【发散想象（imagination）】【变体（variants）】三个字段从'短句标签'升级为'方法论级内容'。"
    "只重写这三个字段，其他字段一律保持原样。\n"
    "深度要求：\n"
    "- mechanism ≥60 字，写满四要素：①触发条件（什么情境/观众状态）②点名具体心理或传播机制"
    "（如 反常识冲突/认知失调/信息缺口/损失厌恶/峰终定律/社交货币/情绪传染/悬念-释放/反差/身份认同，"
    "禁止只写'激发好奇心/吸引注意力/引发共鸣'）③位置关联（结合来源句在片中的位置、目标人群说明"
    "为什么此刻有效）④适用边界或反例。\n"
    "- imagination ≥50 字，写满三要素：①具体迁移场景（哪个品类/人群/平台）②在该场景的具体改编动作"
    "③预期效果差异。禁止'可迁移到其他领域'这类场景置换套话。\n"
    "- variants：2~3 条，每条 ≥15 字、互相不相似，必须是'场景 + 具体改法'，不得只是换词。\n"
    "只输出 JSON：{\"mechanism\": \"...\", \"imagination\": \"...\", \"variants\": [{\"text\": \"...\"}, ...]}"
)


async def _deepen_element(
    item: dict[str, Any], issues: list[str], context: dict[str, Any], *, model: str
) -> dict[str, Any] | None:
    """harness 定点深度化：R15 违规元素单元素深度重写，返回机制/想象/变体（未达标则 None）。"""
    payload = {
        k: item.get(k)
        for k in ("code", "name", "text", "mechanism", "variants", "imagination")
        if item.get(k) is not None
    }
    user = (
        "【元素当前内容】\n" + json.dumps(payload, ensure_ascii=False)
        + "\n\n【深度问题反馈】\n" + "\n".join(f"- {f}" for f in issues)
        + "\n\n【来源上下文（供位置关联与机制分析）】\n"
        + json.dumps(context, ensure_ascii=False)
        + "\n\n按要求深度化后，只输出 mechanism、imagination 与 variants 三个字段的 JSON。"
    )
    try:
        data, _info = await complete_json(
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
    """曲线统计点（峰/谷/反转/悬念）+ 模型叙事点（带 line_no）合并去重。"""
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


# ---------------- 单层调用 ----------------

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

    schema_hint = json.dumps(SCHEMAS[layer], ensure_ascii=False, indent=1)
    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": f"{user_text}\n\n必须严格输出 JSON 对象，字段结构如下（不得增删字段名，取不到的时间写 0）：\n{schema_hint}",
        },
    ]

    last_exc = ""
    attempts = (0.2, 0.6, 0.2, 0.6)  # 4 次尝试：provider 5xx 多为瞬时故障，缺重试会整层失败
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
            last_exc = str(exc)
            logger.warning("三层链路 L%s 调用失败（第 %s 次）：%s", layer, attempt + 1, exc)
    return False, {"layer_error": last_exc}, {"items": 0, "prompt_version": version}


# ---------------- 主流程 ----------------

async def purge_three_layer(db: AsyncSession, video_id) -> None:
    """清掉本片的三层产物（脚本层整片重写 + 本片溯源），积木库行按 code 覆盖不删除。"""
    await db.execute(delete(M.RefElementSource).where(M.RefElementSource.source_video_id == video_id))
    script_ids = (
        await db.execute(select(M.ScriptScript.id).where(M.ScriptScript.video_id == video_id))
    ).scalars().all()
    if script_ids:
        # 显式按依赖顺序清理，不依赖 DB 级 ON DELETE CASCADE（异步连接下 PRAGMA 未必生效）
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
    """执行 L1~L6 并把结果写入三层表，返回落库统计与校验证据。"""

    async def report(stage: str, pct: int, msg: str) -> None:
        if progress is not None:
            try:
                await progress(stage, pct, msg)
            except Exception:  # noqa: BLE001
                logger.debug("三层链路进度回调失败", exc_info=True)

    # 先清旧产物再重建原料：script_sentence.raw_sentence_id → raw_transcript_sentence 为 NO ACTION，
    # 若先删 raw_* 会撞上上一轮遗留的脚本句子外键（IntegrityError），故必须先 purge 本片产物。
    await purge_three_layer(db, video.id)

    if ctx is None:
        if manifest:
            ctx = await persist_raw_layer(db, video, manifest)
        else:
            ctx = await load_raw_context(db, video.id)
            if not ctx["sentences"]:
                ctx = await persist_raw_layer(db, video, {})
    if not ctx.get("sentences"):
        return {"ok": False, "error": "保真原料层无可用转写句子（raw_transcript_sentence 为空），无法执行拆解", "counts": {}, "verdicts": []}
    raw_sentences: list[dict[str, Any]] = ctx["sentences"]
    shots: list[dict[str, Any]] = list(ctx["shots"])
    duration_ms: int = int(ctx["duration_ms"] or video.duration_ms or 0)

    # 短碎片合并：ASR 转写常出现不足 8 字的碎片行（如"这也能为此"），而"句子级还原"口径与
    # script_sentence.quote 的 DB CHECK（length(trim(quote)) >= 8）都要求完整句。
    # 这里把连续碎片合并为"句子单元"：时间锚取首尾、文本取拼接、raw_sentence_id 锚首句 id，
    # 各层提示词与行号口径统一以单元为单位，保证原话引用率 100% 且 quote 可校验。
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
            # 组内含画面字幕句（ocr）则整组视为画面字幕来源，保证 L4.5 来源标记可见
            "source": "ocr" if any(str(x.get("source") or "") == "ocr" for x in group) else "asr",
        })

    for s in raw_sentences:
        buf.append(s)
        if len(_unit_text(buf)) >= MIN_QUOTE_LEN:
            _emit(buf)
            buf = []
    if buf:
        if units:  # 尾部残句并入前一单元，避免产生不足 8 字的句子
            tail = buf
            units[-1]["end_ms"] = int(tail[-1]["end_ms"])
            units[-1]["text"] = units[-1]["text"] + _unit_text(tail)
            units[-1]["raw_ids"].extend(str(x["id"]) for x in tail)
            units[-1]["raw_seqs"].extend(int(x["seq"]) for x in tail)
        else:
            _emit(buf)
    for i, u in enumerate(units, start=1):
        u["seq"] = i  # 行号按合并后重排，全链路以此为准
    # 来源标记：画面字幕（raw source='ocr'）→ 画面字幕；英文占比高 → 疑似 BGM 歌词
    warnings: list[str] = []  # 提前初始化（units 分流告警需要，随后续层共用）
    # 口播充足度分流：口播单元 ≥6 时画面文字（OCR 弹幕/UI 字幕）只作画面参考、
    # 不进句子层（避免直播弹幕"人气榜/抽奖"混入台词）；口播稀少（纯 BGM 视频）时
    # 画面字幕才是真实叙事，保留进句子层
    asr_count = sum(1 for u in units if str(u.get("source") or "") != "ocr")
    # BGM/氛围型判定（扩品类验证·舞狮 43s 暴露）：
    # 口播≤1句 + OCR 去重后≤2条（覆盖水印/标题，如"广西藤县狮王 高桩舞狮精彩绝伦无与伦比"
    # 全程覆盖）→ 纯氛围视频：口播是背景音乐歌词/环境音、字幕是水印，都不是叙事台词。
    # 句子层置空，拆解走纯画面场景分支（L3 按画面场景分段、逐句区写画面内容行），
    # 避免 OCR 水印当台词反复逐句、乱码歌词被硬编语义（编导审稿红线）。
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
                "BGM/氛围型视频（口播≤1句 + OCR 为覆盖水印/标题）：句子层置空，"
                "拆解改走纯画面场景分支（L3 按画面场景分段、逐句区写画面内容行）")
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
    line_map = {int(s["seq"]): s for s in sentences}

    tags = (await db.execute(select(M.LibTag.code, M.LibTag.dimension))).all()
    raw_by_id = {str(s["id"]): s for s in sentences}
    # R8 逐字链的原料：未合并的原始转写句（按 seq 升序），用于判定 quote 是否为
    # 原话连续子串（跨句拼接合法），以及跨句时实际覆盖的 raw 句区间。
    raw_chain = [
        {"id": str(s["id"]), "seq": int(s["seq"]), "text": str(s["text"])} for s in raw_sentences
    ]
    raw_frag_by_id = {str(s["id"]): s for s in raw_sentences}
    validator = validate.Validator(
        duration_ms=duration_ms,
        raw_by_seq=line_map,
        raw_by_id=raw_by_id,
        raw_chain=raw_chain,
        shot_descs=[s["desc"] for s in shots],
        whitelists=validate.load_whitelists([(c, d) for c, d in tags]),
    )

    # 释放写事务，避免长时间持锁阻塞其他连接（ai_usage_logs 等）写入
    await db.commit()
    verdicts: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    quality: dict[str, Any] = {}

    title = str(video.title or "")
    base_meta = f"标题：{title or '（无）'}\n平台：{ctx['platform']}\n片长：{duration_ms}ms（约 {round(duration_ms / 1000, 1)} 秒）"
    transcript = _fmt_transcript(sentences)
    # 画面参考（L1 事实锚定的关键输入）：场景描述 + 画面 OCR 文字
    # 口播常是代词密集的对话（你/她/我），真实人物名（如"文成""刘姐"）与事件
    # （如"母带曝光"）只出现在画面标题/弹幕/字幕里——不给 L1 就会泛化误读核心思想。
    visual_ref_lines = []
    for _sh in shots[:8]:
        _d = str(_sh.get("desc") or "").strip()
        _ov = str(_sh.get("text_overlay") or "").strip()
        if not _d and not _ov:
            continue
        _line = f"场景{_sh.get('seq')}（{int(_sh.get('start_ms') or 0) / 1000:.0f}s）：{_d}"
        if _ov:
            _line += f"｜画面文字：{_ov[:150]}"
        visual_ref_lines.append(_line)
    visual_ref = "\n".join(visual_ref_lines) or "（无）"

    # ---------- L1 本片定调 ----------
    await report("L1", 52, "三层链路 · L1 本片定调…")
    ok, data1, q1 = await _run_layer(
        db, video=video, analysis_id=analysis_id, layer=1,
        user_text=f"【视频元信息】\n{base_meta}\n\n【画面参考（关键人物/事件线索，必须用于 key_facts）】\n{visual_ref}\n\n【#03 逐句转写（含毫秒）】\n{transcript}",
        model=model, array_keys=LAYER_ARRAY_KEYS[1], max_tokens=8192,
    )
    quality["L1"] = q1
    if not ok:
        return {"ok": False, "error": data1.get("layer_error", "L1 失败"), "counts": counts, "verdicts": verdicts}

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
    verdict = validator.validate("script_script", {"core_idea": script.core_idea, "content_trend": script.content_trend, "target_audience": script.target_audience, "summary": script.summary, "duration_ms": duration_ms}, label=f"script:{title[:20]}")
    if not verdict.accepted:
        return {"ok": False, "error": "L1 结构校验未通过：" + "；".join(verdict.hard), "counts": counts, "verdicts": [verdict.as_dict()]}
    # ---- L1 核心思想事实锚定（编导自审①：核心思想误读，如直播八卦读成'揭秘产品'） ----
    # key_facts 实体须出现在 core_idea 中；未锚定即判解读漂移，触发定点重写
    # （一次小调用，把 core_idea/content_trend/target_audience/summary 一起按事实修正）。
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
                f"核心思想未锚定关键事实实体（{entities[:5]}），判为解读漂移，触发定点重写")
            try:
                from app.ai import chat as _ai_chat  # 延迟导入
                _facts_txt = "\n".join(
                    f"- {f.get('entity')}：{f.get('relation')}（证据：{f.get('evidence')}）"
                    for f in kfs if isinstance(f, dict)
                ) or "（无）"
                _rew = await _ai_chat(
                    [
                        {"role": "system", "content": (
                            "你是爆款短视频拆解专家。给定视频关键事实清单，重写定调字段。"
                            "核心思想必须围绕清单中的真实人物与事件（讲清'谁和谁之间关于什么的事'），"
                            "禁止泛化成脱离事实的通用道理；内容走向/目标人群/摘要同步按事实修正。"
                            "只输出 JSON：{\"core_idea\":\"\",\"content_trend\":\"\","
                            "\"target_audience\":\"\",\"summary\":\"\"}")},
                        {"role": "user", "content": (
                            f"【关键事实清单】\n{_facts_txt}\n\n【原定调（疑似漂移）】\n"
                            f"core_idea: {script.core_idea}\ncontent_trend: {script.content_trend}\n"
                            f"target_audience: {script.target_audience}\nsummary: {script.summary}")},
                    ],
                    model=model, max_tokens=2048, json_mode=True, timeout=120,
                    scene="tl1_anchor_rewrite",
                )
                _rd = json.loads((_rew or {}).get("reply") or "{}")
                if _rd.get("core_idea"):
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
    counts["script_script"] = 1
    verdicts.append(verdict.as_dict())
    await db.commit()

    # ---------- L2 连续情绪曲线 ----------
    interval_ms = int(data1.get("sample_interval_ms") or 0) or emotion.pick_interval(duration_ms)
    interval_ms = min(max(interval_ms, 200), emotion.MAX_INTERVAL_MS)
    grid = emotion.build_grid(duration_ms, interval_ms)
    await report("L2", 58, f"三层链路 · L2 情绪曲线采样（{len(grid)} 点，间隔 {interval_ms}ms）…")
    # 分窗推理：单次要求模型一次性输出全片百余个采样点，在免费档上会超时/被网关拒绝；
    # 改为按时间窗口分批请求，服务端按 t_ms 归并成同一条等间隔连续曲线。
    chunk_ms = 20000
    windows: list[tuple[int, int]] = []
    start = 0
    while start < duration_ms:
        windows.append((start, min(duration_ms, start + chunk_ms)))
        start += chunk_ms
    if not windows:
        windows = [(0, duration_ms)]
    l2_agg: dict[str, Any] = {
        "chunks": len(windows), "ok_chunks": 0, "layer_errors": [],
        "model_points": 0, "prompt_version": None, "batches": [],
    }
    model_series: dict[int, float] = {}
    l2_turn_points: list[Any] = []
    for idx, (w_start, w_end) in enumerate(windows, start=1):
        part = [s for s in sentences if int(s["end_ms"]) > w_start and int(s["start_ms"]) < w_end] or sentences[:2]
        part_energy = [
            e for e in ctx["energy"] if w_start - 3000 <= int(e["t_ms"]) <= w_end + 3000
        ]
        l2_user = (
            f"【采样参数】interval_ms={interval_ms}，sample_count={len(grid)}，片长={duration_ms}ms\n"
            f"【本批窗口】第 {idx}/{len(windows)} 批：window_start_ms={w_start}，window_end_ms={w_end}。"
            f"intensity_series 只输出本窗口内按 interval_ms 等间隔的采样点（可含两端，越界点不要输出），"
            f"每点格式 [t_ms, 强度0~10]。\n\n"
            f"【#03 逐句转写（含毫秒，行号即 line_no）】\n{_fmt_transcript(part)}\n\n"
            f"【音频能量采样（t_ms:energy，0~1，限本窗口前后 3 秒）】\n{_fmt_energy(part_energy)}"
        )
        await report("L2", 58, f"三层链路 · L2 情绪曲线采样（窗口 {idx}/{len(windows)}）…")
        ok2, data2, q2 = await _run_layer(
            db, video=video, analysis_id=analysis_id, layer=2,
            user_text=l2_user, model=model, array_keys=LAYER_ARRAY_KEYS[2], max_tokens=8192,
        )
        if idx == 1:
            quality["L2"] = q2
        if ok2:
            l2_agg["ok_chunks"] += 1
            l2_agg["prompt_version"] = q2.get("prompt_version")
            got = 0
            for point in data2.get("intensity_series") or []:
                try:
                    t, v = int(point[0]), _clamp_intensity(point[1])
                except (TypeError, ValueError, IndexError):
                    continue
                if t < w_start or t > w_end:
                    continue
                index = emotion.index_of_t(grid, t)
                if index not in model_series:
                    model_series[index] = v
                    got += 1
            l2_agg["batches"].append({"window": [w_start, w_end], "points": got})
            l2_turn_points.extend(data2.get("turn_points") or [])
        else:
            l2_agg["layer_errors"].append(f"窗口{idx}({w_start}-{w_end}ms)：{data2.get('layer_error')}")
    l2_agg["model_points"] = len(model_series)
    quality["L2"] = {**(quality.get("L2") or {}), **l2_agg}
    ok, data2 = bool(l2_agg["ok_chunks"]), {}

    # 曲线以模型序列为骨架，缺失/越界点用句子强度插值补齐（插值点占比 ≤30% 校验）
    # 模型未给出序列时，退回"音频能量锚点法"：按句子区间内能量分位映射到 2~8 强度（可复算、非主观命名）
    fallback_used = ""
    if not model_series and ctx["energy"]:
        e_grid = emotion.resample_energy(grid, [(int(e["t_ms"]), float(e["energy"])) for e in ctx["energy"]])
        e_lo, e_hi = min(e_grid), max(e_grid)
        for s in sentences:
            i0 = emotion.index_of_t(grid, int(s["start_ms"]))
            i1 = emotion.index_of_t(grid, int(s["end_ms"]))
            seg = e_grid[min(i0, i1): max(i0, i1) + 1] or [e_grid[i0]]
            ratio = (max(seg) - e_lo) / (e_hi - e_lo) if e_hi > e_lo else 0.5
            model_series[emotion.index_of_t(grid, int(s["start_ms"]))] = round(2.0 + 6.0 * ratio, 1)
        fallback_used = "audio_energy"

    spans = [(int(s["start_ms"]), int(s["end_ms"]), float(model_series.get(emotion.index_of_t(grid, int(s["start_ms"])), 4.0))) for s in sentences]
    interpolated, interp_idx = emotion.sample_from_spans(grid, spans)
    values = [model_series.get(i, interpolated[i]) for i in range(len(grid))]
    interp_ratio = round(len(interp_idx) / max(1, len(grid)), 4)
    method = fallback_used or "model"
    if ctx["energy"]:
        energy_values = emotion.resample_energy(grid, [(int(e["t_ms"]), float(e["energy"])) for e in ctx["energy"]])
        values = emotion.mix_with_energy(values, energy_values)
        method = fallback_used or "hybrid"

    stats = emotion.analysis(values, grid)
    degenerate = (stats["series_max"] or 0) - (stats["series_min"] or 0) < 1.5
    if degenerate:
        warnings.append(
            f"情绪曲线近乎平直（强度区间 {stats['series_min']}~{stats['series_max']}，"
            f"形状 {stats['shape']}，模型覆盖 {len(model_series)}/{len(grid)} 点）"
        )
    curve = M.ScriptEmotionCurve(
        script_id=script.id,
        sample_interval_ms=interval_ms,
        duration_ms=duration_ms,
        sample_count=len(grid),
        intensity_series=[[grid[i], round(float(values[i]), 1)] for i in range(len(grid))],
        series_min=stats["series_min"],
        series_max=stats["series_max"],
        shape=stats["shape"],
        peak_position_ratio=stats["peak_position_ratio"],
        peak_count=stats["peak_count"],
        valley_count=stats["valley_count"],
        baseline_intensity=stats["baseline_intensity"],
        method=method,
    )
    db.add(curve)
    await db.flush()
    counts["script_emotion_curve"] = 1
    quality["curve"] = {
        "sample_interval_ms": interval_ms,
        "sample_count": len(grid),
        "interpolated_points": len(interp_idx),
        "interpolated_ratio": interp_ratio,
        "shape": stats["shape"],
        "peak_count": stats["peak_count"],
        "valley_count": stats["valley_count"],
        "peak_position_ratio": stats["peak_position_ratio"],
        "method": method,
        "model_points": len(model_series),
        "fallback": fallback_used,
        "model_call_ok": bool(ok),
        "degenerate": degenerate,
    }
    if interp_ratio > 0.30:
        warnings.append(f"曲线插值点占比 {interp_ratio} 超过 30% 上限（模型序列覆盖不足）")
    # 冻结曲线口径：L5 各积木循环会复用同名局部变量 values，L6 组合模板需要按位置比取曲线强度，
    # 因此这里显式保存一份不被后续覆盖的曲线网格与强度序列。
    curve_grid, curve_values = list(grid), [float(v) for v in values]

    # ---------- L3 段落切分 ----------
    await report("L3", 64, "三层链路 · L3 段落切分…")
    # harness 分段建议（确定性信号，软参考）：
    # - 口播充足（≥6 句）：句间停顿 >1500ms + 口播结束点（最后一句 ASR 结束后仍有较长
    #   无口播画面段，如美妆类"吐槽→技巧展示"）。口播充足时不引用场景切换：
    #   直播/剧情镜头切换频繁，是噪音不是叙事阶段。
    # - 口播稀少（<6 句）：引用画面场景切换秒点（叙事主要靠画面推进）。
    seg_hints: list[int] = []
    dur_ms = int((manifest or {}).get("duration_ms") or 0)
    if asr_count >= 6:
        for a, b in zip(units, units[1:]):
            gap = (b.get("start_ms") or 0) - (a.get("end_ms") or 0)
            if gap > 1500:
                seg_hints.append(int(round(int(b["start_ms"]) / 1000)))
        last_end = max((int(u.get("end_ms") or 0) for u in units), default=0)
        if dur_ms > 0 and last_end < dur_ms * 0.9 and (dur_ms - last_end) >= 5000:
            # 口播在此结束、后段为画面演示/技巧展示段（无口播）→ 强分段信号
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
        f"【L1 结论】\n{json.dumps({k: data1.get(k) for k in ('core_idea','content_trend','target_audience','hook_type','narrative_order')}, ensure_ascii=False)}\n\n"
        f"【#03 逐句转写（含毫秒，行号即 line_no）】\n{transcript}\n\n片长：{duration_ms}ms"
        + hint_text
    )
    if bgm_ambience:
        # BGM/氛围型：无口播可锚定，跳过 L3 模型分段（flash 无台词可切必乱切），
        # harness 按画面场景（scenes）确定性切段——时间精确归 harness、语义归画面场景。
        _scenes = ((manifest or {}).get("scenes") or [])
        if not _scenes:
            _scenes = [{"start_ms": 0, "end_ms": dur_ms, "subject": str(video.title or "")[:12], "action": "", "change_note": ""}]
        segments_json = [
            {
                "seq": i, "seg_type": "高潮" if i == len(_scenes) else "铺垫",
                "title": (f"{sc.get('subject') or ''}{sc.get('action') or ''}")[:28],
                "start_ms": int(sc.get("start_ms") or (0 if i == 1 else segments_json[-1]["end_ms"] if 'segments_json' in dir() else 0)),
                "end_ms": int(sc.get("end_ms") or dur_ms),
                "purpose": f"画面场景 {i}/{len(_scenes)}（{sc.get('change_note') or '画面延续'}）",
                "summary": f"画面场景 {i}：{sc.get('subject') or ''}{sc.get('action') or ''}",
                "emotion_level": stats["baseline_intensity"] or 0,
            }
            for i, sc in enumerate(_scenes, start=1)
        ]
        ok3, data3, q3 = True, {"segments": segments_json}, {
            "items": len(segments_json), "skipped": "bgm_ambience", "prompt_version": 1}
        quality["L3"] = q3
        await report("L3", 62, f"三层链路 · L3 画面场景分段（BGM/氛围型，{len(segments_json)} 段）…")
    else:
        ok3, data3, q3 = await _run_layer(
        db, video=video, analysis_id=analysis_id, layer=3,
        user_text=l3_user, model=model, array_keys=LAYER_ARRAY_KEYS[3], max_tokens=16384,
    )
    quality["L3"] = q3
    segments_json = (data3.get("segments") or []) if ok3 else []
    # 段数契约强校验：目标段数按片长折算（编导颗粒度：50s 至少 5 个功能段），
    # 低于目标自动重试一次并附校验反馈（flash 免费档常见 1~4 段，需强反馈细分）
    target_seg_n = max(L3_SEGMENT_MIN, min(8, round(duration_ms / 10000)))
    if not bgm_ambience and len(segments_json) < target_seg_n:
        retry_hint = (
            f"\n\n【校验反馈】上次返回 {len(segments_json)} 段，本片 {duration_ms / 1000:.0f}s"
            f"按颗粒度应拆 {target_seg_n}~{L3_SEGMENT_MAX} 个功能段"
            f"（{duration_ms / 1000:.0f}s 视频至少 {target_seg_n} 段）。"
            f"请重新切分：段落必须按行号边界（#nn）切分，段数 {target_seg_n}~{L3_SEGMENT_MAX}，"
            f"覆盖全片、首尾相接、不重叠；**宁可多分真实话题阶段，不要糊成大段**"
            f"（如话题从 A 转到 B 就是新段边界）。"
        )
        await report("L3", 66, f"三层链路 · L3 段数不足（{len(segments_json)}<{target_seg_n}），自动重试…")
        ok3b, data3b, q3b = await _run_layer(
            db, video=video, analysis_id=analysis_id, layer=3,
            user_text=l3_user + retry_hint, model=model,
            array_keys=LAYER_ARRAY_KEYS[3], max_tokens=16384,
        )
        quality["L3_retry"] = {
            **q3b,
            "first_count": len(segments_json),
            "retried": True,
        }
        if ok3b:
            segments_json = data3b.get("segments") or []
            quality["L3"] = q3b
            if len(segments_json) >= target_seg_n:
                await report("L3", 67, f"三层链路 · L3 重试达标（{len(segments_json)} 段）…")
    # 口播段细分：口播充足（≥6句）但口播段 <2（flash 常把整段口播并成1段，如美妆 0~42s
    # 7 句一口气）→ 按叙事阶段（开场引入→主体推进→收尾/转折）细分重试一次。
    # 编导视角：长口播只有 1 段 = 拆解颗粒度不足，情绪/手法变化全糊在一段里。
    asr_seg_n = sum(
        1 for s in segments_json
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
            db, video=video, analysis_id=analysis_id, layer=3,
            user_text=l3_user + retry_hint2, model=model,
            array_keys=LAYER_ARRAY_KEYS[3], max_tokens=16384,
        )
        quality["L3_retry2"] = {
            **q3c,
            "first_asr_seg_n": asr_seg_n,
            "retried": True,
        }
        if ok3c:
            segments_json = data3c.get("segments") or []
            quality["L3"] = q3c
    if len(segments_json) > L3_SEGMENT_MAX:
        warnings.append(
            f"L3 段数 {len(segments_json)} 超过上限 {L3_SEGMENT_MAX}（保留全部段落，未截断）"
        )
    if not segments_json:
        segments_json = [
            {"seq": 1, "seg_type": "钩子", "title": title[:12] or "全片", "line_from": 1,
             "line_to": max(1, len(sentences)), "purpose": "模型未返回段落，按全片兜底为一个段落",
             "summary": "全片口播", "hook_point": True, "payoff_point": True,
             "emotion_level": stats["baseline_intensity"] or 0}
        ]
        warnings.append("L3 未返回可用段落（重试后仍为空），已按全片兜底")

    # --- 模型分段质量检测 + harness 停顿兜底 ---
    # flash 免费档常把整段口播拆成多段但行号全重叠（同一句区间重复落多段，
    # 如美妆 7 句被拆 6 段 line 全是 1~7），harness 丢弃后颗粒度反而更粗。
    # 此时按口播句间停顿（gap≥1.5s）确定性重建口播段，画面段仍由补段逻辑处理。
    _ordered = [
        x for x in segments_json
        if (x.get("line_from") is not None and x.get("line_from") != "")
        or (x.get("line_to") is not None and x.get("line_to") != "")
    ]
    # 覆盖缺口检测：任何口播句未落入任何段（如模型把 0~28s 与 38~42s 口播并成一段、
    # 或 2 段只覆盖前 6 句丢掉末句质问）→ 一律按停顿兜底重建，保证每句口播都有段落归属
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
    # 停顿基准颗粒度：按 gap≥1.5s 切分的口播段数（编导最小可执行颗粒度）
    _pause_n = 1
    _pe = None
    for _s in sentences:
        if _pe is not None and int(_s["start_ms"]) - _pe >= 1500:
            _pause_n += 1
        _pe = int(_s["end_ms"])
    # 段内停顿检测：任何含 line 段在其句子区间内部跨越 gap≥1.5s 的长停顿
    # （如把 0~42s 口播并成一段、内部夹着 10s 空档）→ 颗粒度过粗，触发兜底。
    # 正确分段（0~28 / 38~42）每段内部无长停顿，不受影响。
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
                f"L3 模型分段漏覆盖口播句 {[int(x['seq']) for x in _uncovered]}，"
                f"harness 按口播停顿确定性兜底重建")
        elif _pause_inside:
            warnings.append(
                "L3 模型分段颗粒度过粗（某段内部跨越长停顿），"
                "harness 按口播停顿确定性兜底重建")
        if len(_ordered) >= 3 and _valid < 2:
            warnings.append(
                f"L3 模型分段质量差（{len(_ordered)} 段仅 {_valid} 段行号有效），"
                f"harness 按口播停顿（gap≥1.5s）确定性兜底重建")
        if _uncovered or _pause_inside or (len(_ordered) >= 3 and _valid < 2):
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
                    "seq": i, "seg_type": "干货", "title": "",
                    "line_from": int(_g["first"]["seq"]), "line_to": int(_g["last"]["seq"]),
                    "purpose": "harness 按口播停顿确定性重建（模型分段质量差时兜底）",
                    "summary": f"口播句 {_g['first']['seq']}~{_g['last']['seq']}",
                    "emotion_level": stats["baseline_intensity"] or 0,
                }
                for i, _g in enumerate(_segs, start=1)
            ]
            warnings.append(f"L3 harness 兜底重建 {len(segments_json)} 个口播段（按停顿）")

    # --- harness 段落时间锚定（语义归模型，精确时间归 harness）---
    # 契约：段 1 从 0 起、首尾相接、严格单调（_TL3 第 1 条）。
    # 模型给非法 line 号时不再兜底首句（会造成 0 起重叠），而是退化锚定到"前一段末尾之后"；
    # from/to 倒置时交换；本段起点不得早于前段终点（句子级别单调强制）。
    seg_rows: list[M.ScriptSegment] = []
    _prev_last: dict[str, Any] | None = None
    for item in segments_json:
        has_lines = item.get("line_from") is not None or item.get("line_to") is not None
        if not has_lines:
            # 纯画面段（口播结束后的画面演示/技巧展示/收尾段）：时间锚定，不锚定口播句
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
            row = M.ScriptSegment(
                script_id=script.id,
                seq=int(item.get("seq") or len(seg_rows) + 1),
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
                f"L3 段{item.get('seq')} 为纯画面段（无口播锚点，{st_ms}ms~{en_ms}ms，"
                f"时间锚定保留）"
            )
            continue
        s_first = _line_to_sentence(sentences, item.get("line_from"))
        s_last = _line_to_sentence(sentences, item.get("line_to"))
        _bad_from = item.get("line_from") is not None and s_first is None
        _bad_to = item.get("line_to") is not None and s_last is None
        if s_first is None:
            # 非法 from（无数字可解析）：首段锚定首句（保证 0 起），后续段退化到前段尾句之后；
            # 已到最后句时锚定末尾句保留语义（模型对末尾的语义细分，如干货/CTA 段）
            s_first = _next_sentence_after(sentences, _prev_last) or _prev_last
            if s_first is None:
                continue
        if s_last is None:
            s_last = _next_sentence_after(sentences, s_first) or sentences[-1]
        # 倒置保护：from > to 时交换，避免负区间
        if int(s_first["seq"]) > int(s_last["seq"]):
            s_first, s_last = s_last, s_first
        # 单调强制：本段起点不得早于前段终点（句子 seq 级别）
        if _prev_last is not None and int(s_first["seq"]) <= int(_prev_last["seq"]):
            s_first = _next_sentence_after(sentences, _prev_last) or _prev_last
            if int(s_last["seq"]) <= int(s_first["seq"]):
                s_last = _next_sentence_after(sentences, s_first) or sentences[-1]
        # 重复覆盖防护：模型把同一句切成多段（如多段 line_to 相同）时，
        # 仅保留首段，后续重叠段跳过（防止同一叙事在时间轴上重复落 4 段）
        if seg_rows and int(s_first["seq"]) <= int(seg_rows[-1].end_sentence_seq):
            warnings.append(
                f"L3 段{item.get('seq')} 与段{seg_rows[-1].seq} 句子区间重叠（{s_first['seq']}≤{seg_rows[-1].end_sentence_seq}），harness 已合并跳过"
            )
            continue
        if _bad_from or _bad_to:
            warnings.append(
                f"L3 段{item.get('seq')} 模型行号非法（from={item.get('line_from')!r} to={item.get('line_to')!r}），"
                f"已由 harness 锚定到句 {s_first['seq']}~{s_last['seq']}"
            )
        start_ms, end_ms, _ = validate.correct_time(
            s_first["start_ms"], s_last["end_ms"], (int(s_first["start_ms"]), int(s_last["end_ms"]))
        )
        seg_type = str(item.get("seg_type") or "").strip()
        if seg_type not in validator.wl.get("seg_type", set()):
            seg_type = "铺垫"
        row = M.ScriptSegment(
            script_id=script.id,
            seq=int(item.get("seq") or len(seg_rows) + 1),
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

    # --- harness 兜底补段：口播充足时，口播结束后仍有 ≥3s 无口播画面段未被段落覆盖 ---
    # 模型输出格式受限（段落须锚定口播行号）表达不了纯画面段，这里由 harness 确定性补齐，
    # 保证"覆盖全片、首尾相接"契约（如美妆类：吐槽口播段 → 技巧展示画面段）。
    if asr_count >= 6 and seg_rows:
        last = seg_rows[-1]
        last_asr_end = max((int(u.get("end_ms") or 0) for u in units), default=0)
        if last.end_ms < dur_ms - 2000 and last_asr_end >= last.end_ms - 500:
            st = max(last.end_ms, last_asr_end)
            en = dur_ms
            if en - st >= 3000:
                row = M.ScriptSegment(
                    script_id=script.id,
                    seq=len(seg_rows) + 1,
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
                v = validator.validate(
                    "script_segment",
                    {
                        "seq": len(seg_rows) + 1,
                        "seg_type": "干货",
                        "title": None,
                        "start_ms": st,
                        "end_ms": en,
                        "start_sentence_seq": last.end_sentence_seq,
                        "end_sentence_seq": last.end_sentence_seq,
                        "purpose": row.purpose,
                        "summary": row.summary,
                        "hook_point": 0,
                        "payoff_point": 0,
                    },
                    label=f"seg{row.seq}:画面补段",
                )
                verdicts.append(v.as_dict())
                if v.accepted:
                    db.add(row)
                    seg_rows.append(row)
                    warnings.append(
                        f"L3 harness 自动补纯画面段（{st}ms~{en}ms，口播结束后的画面段，"
                        f"模型输出格式无法表达，由 harness 确定性补齐）"
                    )
    # --- harness 确定性碎片合并：模型逐句切段（单句+短时长+句子连续+无停顿）→ 并入前段 ---
    # 背景：flash 分段不稳定——同一视频可能判 1 段（口播连续）或 7 段（逐句切，全标"铺垫"）。
    # 段落骨架是结构层，不应依赖模型临场发挥：这里用确定性规则把碎片段合并回连续口播段。
    # 纯画面补段（start_sentence_seq 复用尾句号 → 与上段不连续）天然被排除，不会误并。
    if len(seg_rows) > 1:
        _i = 1
        while _i < len(seg_rows):
            _prev = seg_rows[_i - 1]
            _cur = seg_rows[_i]
            _n_cur = _cur.end_sentence_seq - _cur.start_sentence_seq + 1
            _cur_dur = _cur.end_ms - _cur.start_ms
            _contiguous = _cur.start_sentence_seq == _prev.end_sentence_seq + 1
            # 句间 gap（无口播空隙）：<1500ms 视为连续口播，≥1500ms 视为真实停顿边界
            _gap = 10**9
            if _contiguous:
                _pl = next((s for s in sentences if int(s.get("seq") or 0) == _prev.end_sentence_seq), None)
                _cf = next((s for s in sentences if int(s.get("seq") or 0) == _cur.start_sentence_seq), None)
                if _pl is not None and _cf is not None:
                    _gap = int(_cf.get("start_ms") or 0) - int(_pl.get("end_ms") or 0)
            if _contiguous and _n_cur <= 1 and _cur_dur < 10000 and _gap < 1500:
                warnings.append(
                    f"L3 harness 合并碎片段：段{_cur.seq}（{_cur.start_ms/1000:.0f}s~{_cur.end_ms/1000:.0f}s，"
                    f"{_cur.seg_type}）为单句短段且与前段连续无停顿，已并入段{_prev.seq}"
                )
                _prev.end_ms = max(_prev.end_ms, _cur.end_ms)
                _prev.end_sentence_seq = max(_prev.end_sentence_seq, _cur.end_sentence_seq)
                seg_rows.pop(_i)
                try:
                    await db.delete(_cur)
                except Exception:
                    pass
            else:
                _i += 1
        # 合并后 seq 重排（避免跳号，fs 段落头 1..N 连续）
        for _n, _s in enumerate(seg_rows, start=1):
            _s.seq = _n
    # --- BGM/氛围型 harness 确定性修复（编导审稿 v2 发现）---
    # 峰值按段序递增（引入<展示<高潮）：每段从情绪曲线取均值，再按段序单调递增归一化
    # （编导验收线：高潮段=全片最高、引入段更低）
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
                # 段序递增：第 i 段 = 均值 × (0.85 + 0.15*i/(n-1))（引入 0.85→高潮 1.0）
                _mult = 0.85 + (0.15 * _i / max(_n - 1, 1))
                _s.emotion_peak = round(min(_base * _mult, 10.0), 1)
    await db.flush()
    counts["script_segment"] = len(seg_rows)
    await db.commit()
    if not seg_rows:
        return {"ok": False, "error": "L3 段落全部未通过校验", "counts": counts, "verdicts": verdicts}
    if len(seg_rows) < L3_SEGMENT_MIN:
        warnings.append(
            f"L3 落库段数 {len(seg_rows)} 仍低于契约下限 {L3_SEGMENT_MIN}"
            f"（返回 {len(segments_json)} 段，白名单/时间校验过滤 {len(segments_json) - len(seg_rows)} 段）"
        )
    # BGM/氛围型视频强套叙事结构告警（编导自审发现）：口播稀少（asr<6）时，
    # 若段落仍标"高潮/铺垫"等叙事功能词，很可能是把无叙事的画面合集硬套口播叙事模板。
    if asr_count < 6:
        _narr_tags = [s.seg_type for s in seg_rows if s.seg_type in ("高潮", "铺垫", "钩子")]
        if _narr_tags:
            warnings.append(
                f"L3 BGM/氛围型视频（口播仅 {asr_count} 句）段落标签含叙事功能词"
                f"（{','.join(_narr_tags)}）——可能是把无叙事画面合集强套口播叙事结构，"
                f"L4.5 应按画面内容主题分析"
            )
    # 段落连续性校验：契约要求"覆盖全片、首尾相接、不重叠"（_TL3 第 1 条）。
    # 模型给出非法 line 号时 _line_to_sentence 会静默兜底到首句，产生 0 起/重叠段，
    # 直接污染 L6 组合模板的槽位覆盖——这里显式检测并在 L6 前告警。
    _cont = sorted(seg_rows, key=lambda s: (s.start_ms, s.seq))
    for _a, _b in zip(_cont, _cont[1:]):
        if _b.start_ms < _a.end_ms:
            warnings.append(
                f"L3 段落时间重叠：段{_a.seq}({_a.start_ms}-{_a.end_ms}ms) 与 段{_b.seq}"
                f"({_b.start_ms}-{_b.end_ms}ms)（模型 line 号非法致兜底，L6 槽位覆盖可能不完整）"
            )
            break

    def segment_of_line(seq: int) -> M.ScriptSegment | None:
        for seg in seg_rows:
            if seg.start_sentence_seq <= seq <= seg.end_sentence_seq:
                return seg
        return seg_rows[-1]

    # ---------- L4 句子级还原 ----------
    await report("L4", 70, "三层链路 · L4 句子级还原…")
    seg_brief = json.dumps(
        [{k: getattr(s, k) for k in ('seq', 'seg_type', 'title', 'start_sentence_seq', 'end_sentence_seq', 'purpose')} for s in seg_rows],
        ensure_ascii=False,
    )
    n_lo = max(len(sentences), math.ceil(duration_ms / 4000))
    n_hi = max(len(sentences), math.floor(duration_ms / 2400))
    # 分批调用：单批输出可控，避免长输出被 provider 5xx 直接打回（实测整片一次性输出必失败）
    chunk_size = 7
    l4_chunks = [sentences[i:i + chunk_size] for i in range(0, len(sentences), chunk_size)]
    sentences_json: list[dict[str, Any]] = []
    q4_agg: dict[str, Any] = {"chunks": len(l4_chunks), "ok_chunks": 0, "items": 0, "layer_errors": []}
    if bgm_ambience:
        # BGM/氛围型：句子层已置空，无句子可还原，跳过 L4（L4.5 逐句区改画面内容行）
        q4_agg["skipped"] = "bgm_ambience"
        quality["L4"] = q4_agg
    for idx, part in enumerate(l4_chunks, start=1):
        if bgm_ambience:
            break
        await report("L4", 70, f"三层链路 · L4 句子级还原（{idx}/{len(l4_chunks)} 批）…")
        part_transcript = _fmt_transcript(part)
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
            db, video=video, analysis_id=analysis_id, layer=4,
            user_text=l4_user, model=model, array_keys=LAYER_ARRAY_KEYS[4], max_tokens=16384,
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
        return {"ok": False, "error": "L4 全部批次调用失败：" + "；".join(map(str, q4_agg["layer_errors"]))[:300],
                "counts": counts, "verdicts": verdicts}
    # 按行号归并、seq 重排，保证句序与时间轴一致
    def _line_key(it: dict[str, Any]) -> tuple[int, int]:
        raw_line = it.get("line_no")
        try:
            line = int(raw_line)
        except (TypeError, ValueError):
            line = 10 ** 6
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
        start_ms, end_ms, _ = validate.correct_time(raw["start_ms"], raw["end_ms"], (int(raw["start_ms"]), int(raw["end_ms"])))
        function = str(item.get("sentence_function") or "").strip()
        if function not in validator.wl.get("sentence_function", set()):
            function = "过渡"
        row = M.ScriptSentence(
            script_id=script.id,
            segment_id=(segment_of_line(int(raw["seq"])).id if segment_of_line(int(raw["seq"])) else None),
            seq=int(item.get("seq") or len(sentence_rows) + 1),
            raw_sentence_id=raw["id"],
            source_video_id=video.id,
            quote=quote or str(raw["text"]),
            start_ms=start_ms,
            end_ms=end_ms,
            sentence_function=function,
            function_reason=str(item.get("function_reason") or "")[:4000] or "（缺失）",
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
            {**item, "raw_sentence_id": raw["id"], "source_video_id": video.id, "quote": row.quote,
             "start_ms": start_ms, "end_ms": end_ms,
             "emotion_intensity": row.emotion_intensity, "function_reason": row.function_reason,
             "variants": row.variants, "imagination": row.imagination, "sentence_function": function},
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
            ev_verdict = validator.validate("script_evidence", {
                "evidence_type": ev_type, "quote": ev_quote, "content": ev.get("content"),
                "start_ms": start_ms, "end_ms": end_ms,
                "raw_sentence_id": raw["id"], "line_no": raw["seq"],
            }, label=f"句 {raw['seq']} 证据", raw_text=str(raw["text"]), anchor_ids=raw.get("raw_ids") or ())
            verdicts.append(ev_verdict.as_dict())
            if ev_verdict.accepted and str(ev.get("content") or "").strip():
                # 句子主键由 flush 时的 Python 端默认值生成，此处先登记，flush 后再落库
                pending_evidence.append((row, ev_type, ev_quote, start_ms, end_ms, raw["id"]))
    await db.flush()
    for row, ev_type, ev_quote, ev_start, ev_end, raw_id in pending_evidence:
        if row.id is None:  # 句子未落库（校验被拒）时跳过其证据
            continue
        row_ev = M.ScriptEvidence(
            script_id=script.id,
            sentence_id=row.id,
            evidence_type=ev_type,
            raw_sentence_id=raw_id if ev_type == "transcript" else None,
            raw_shot_id=None,
            source_video_id=video.id,
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
        return {"ok": False, "error": "L4 句子全部未通过校验（R8 原话一致/R11 反标签等硬拦截）", "counts": counts, "verdicts": verdicts}

    # 曲线值回填句级强度 + 峰/谷/turn 标记
    for row in sentence_rows:
        idx = emotion.index_of_t(grid, row.start_ms)
        row.emotion_intensity = round(float(values[idx]), 1)
    peak_sentences = {grid[i] for i in stats["peaks"]}
    for row in sentence_rows:
        near = min(peak_sentences, key=lambda t: abs(t - row.start_ms)) if peak_sentences else None
        if near is not None and abs(near - row.start_ms) <= interval_ms and row.is_peak:
            row.is_peak = 1

    # 段落情绪峰值回填
    for seg in seg_rows:
        seg_values = [
            row.emotion_intensity for row in sentence_rows
            if seg.start_sentence_seq <= row.seq <= seg.end_sentence_seq
        ]
        if seg_values:
            seg.emotion_peak = round(max(seg_values), 1)

    # ---------- 转折点 ----------
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
        v = validator.validate("script_turn_point", {"turn_type": row.turn_type, "t_ms": row.t_ms, "intensity": row.intensity, "note": row.note})
        verdicts.append(v.as_dict())
        if v.accepted:
            db.add(row)
            turn_rows.append(row)
    await db.flush()
    counts["script_turn_point"] = len(turn_rows)
    for row in sentence_rows:
        if any(t.sentence_id == row.id and t.turn_type in ("反转", "悬念") for t in turn_rows):
            row.is_turn = 1

    # ---------- L4.5 完整脚本还原（脚本厚度：成文、可读、可直接给编导用）----------
    full_script: str | None = None
    q45: dict[str, Any] = {"errors": []}
    if seg_rows:
        # BGM/氛围型（sentence_rows 为空）也必须出成稿：L4.5 走"无口播"分支写画面内容行
        await report("L4.5", 76, "三层链路 · L4.5 完整脚本还原…")
        try:
            from app.ai import chat as _ai_chat  # 延迟导入，避免模块级循环

            tpl45 = await load_active_prompt(db, LAYER_CODES[45])
            system45 = (
                tpl45.content if tpl45 else TEMPLATES["tl45_script"]["content"]
            ) + THREE_LAYER_CONTRACT
            curve_pts = list(curve.intensity_series or []) if curve else []
            if curve_pts and isinstance(curve_pts[0], str):
                try:
                    curve_pts = json.loads(curve_pts[0])
                except Exception:
                    curve_pts = []
            src_by_raw: dict[str, str] = {
                str(s.get("id")): str(s.get("src") or "口播") for s in sentences
            }
            # 口播充足时画面文字多为弹幕/UI 噪音，L4.5 场景记忆不传 text_overlay（防污染脚本）
            _overlay_is_signal = asr_count < 6
            l45_input = {
                "口播情况": "无口播（BGM/氛围型，句子层为空，逐句区一律写画面内容行）"
                    if bgm_ambience else f"口播 {len(sentences)} 句",
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
                        for p in curve_pts[::4] if isinstance(p, list) and len(p) >= 2
                    ],
                },
                "画面场景记忆": [
                    {
                        "时间ms": [s.get("start_ms"), s.get("end_ms")],
                        "主体": s.get("subject"), "动作": s.get("action"),
                        "风格": s.get("style"),
                        "画面文字": s.get("text_overlay") if _overlay_is_signal else None,
                        "叙事注记": s.get("change_note"),
                    }
                    for s in ((manifest or {}).get("scenes") or [])[:15]
                ],
                "画面动态事件": [
                    {
                        "类型": e.get("event_type"), "时间ms": e.get("t_ms"),
                        "强度": e.get("intensity"), "说明": e.get("note"),
                    }
                    for e in (((manifest or {}).get("frame_plan") or {}).get("dynamic_events")) or []
                ],
                "段落": [
                    {
                        "seq": s.seq, "类型": s.seg_type, "标题": s.title,
                        "起止秒": [round(s.start_ms / 1000, 1), round(s.end_ms / 1000, 1)],
                        "情绪峰值": s.emotion_peak, "目的": s.purpose,
                        # 该段时间范围内的画面素材（供画面段写作，无口播句时引用）
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
                        "seq": s.seq, "原话": s.quote, "功能": s.sentence_function,
                        "理由": s.function_reason, "强度": s.emotion_intensity,
                        "来源": src_by_raw.get(str(s.raw_sentence_id), "口播"),
                        # ASR 存疑标记（编导审稿：素材解读失真——乱码句不得硬编语义）
                        "存疑": _flag_asr_dubious(str(s.quote or "")),
                    }
                    # harness 预过滤：歌词句（BGM 误转写）不喂给模型，避免其为了凑覆盖而编造
                    for s in sentence_rows
                    if src_by_raw.get(str(s.raw_sentence_id), "口播") != "歌词"
                ],
            }
            retried = False
            res45 = None
            for _att in range(3):
                try:
                    res45 = await _ai_chat(
                        [
                            {"role": "system", "content": system45},
                            {
                                "role": "user",
                                "content": json.dumps(
                                    l45_input, ensure_ascii=False, indent=1),
                            },
                        ],
                        model="pro",  # 成稿用 pro（本地 qwen3.5）
                        max_tokens=16384,
                        json_mode=False,
                        timeout=600,  # 本地推理慢，加长超时
                        scene="tl45_script",
                    )
                    break
                except Exception as exc:  # 断连/超时等瞬时故障：退避重试
                    if _att == 2:
                        raise
                    await asyncio.sleep(5 * (_att + 1))
                    logger.warning("L4.5 调用失败，重试 %s/3：%s", _att + 1, str(exc)[:100])
            raw45 = (res45.get("reply") or "").strip()
            if raw45:
                # harness 后处理：剥掉模型多余的 markdown 代码块围栏
                raw45 = re.sub(r"^```(?:markdown)?\s*", "", raw45)
                raw45 = re.sub(r"\s*```\s*$", "", raw45)
                # harness 后处理：清理美妆模板残留术语
                for kw in ["话题热度与变现能力", "直播环境音", "对峙点", "秘密开始抛出", "金哥母带曝光"]:
                    raw45 = raw45.replace(kw, "")
                # harness 后处理：清理多余闭合符和混排内容
                raw45 = re.sub(r"\]\s*\n", "\n", raw45)  # 多余闭合符
                raw45 = re.sub(r"\[实际执行说明\][^\n]*\n?", "", raw45)  # 混排内容
                raw45 = re.sub(r"\[引用自\s*S\d+\]", "", raw45)  # 不明引用
                # 模型偶发把"差异化硬约束"写作规则抄成输出字段：剥字段名、保留正文
                raw45 = re.sub(
                    r"^\s*[-*]?\s*\*\*?差异化硬约束\*\*?[:：]\s*",
                    "补充：",
                    raw45,
                    flags=re.M,
                )
                raw45 = re.sub(r"\n\s*[-*]?\s*\*\*?差异化硬约束\*\*?[:：]\s*", "\n补充：", raw45)
                min_len = (max(1000, len(seg_rows) * 400) if bgm_ambience
                           else max(1600, len(seg_rows) * 500))  # 编导厚度下限：按段数 2段1600/3段1600/4段2000；BGM/氛围型画面内容行为主，下限放宽
                hard_min = int(min_len * 0.9)  # 10% 容差：差一点不整稿作废，记 warning
                seg_marks = raw45.count("###")
                cliche_tail = len(re.findall(r"适用于任何需要[^\n。]*", raw45))
                thickness_hit = len(raw45) < hard_min
                if thickness_hit:
                    q45["warnings"] = (q45.get("warnings") or []) + [
                        f"成稿偏薄 {len(raw45)} 字 < 厚度下限 {min_len}，并入自愈加厚重写"]
                # 占位符残留：模型把提示词模板字面输出（{seg_type}/段N/{title} 等未替换）→ 判不合格
                placeholder_hit = bool(re.search(r"\{[a-z_]+\}|段N\b|{seg|{title", raw45))
                if placeholder_hit or seg_marks < len(seg_rows):
                    # 占位符未替换 / 漏段：自动重试一次（同一输入，期望模型正常输出）
                    if not retried:
                        retried = True
                        q45["warnings"] = q45.get("warnings") or []
                        reason = "占位符未替换（{seg_type}/段N 等字面输出）" if placeholder_hit else \
                            f"漏段（{seg_marks}/{len(seg_rows)}）"
                        q45["warnings"].append(f"首次成稿{reason}，自动重试")
                        logger.info("L4.5 重试：%s seg_marks=%s/%s", reason, seg_marks, len(seg_rows))
                        res45 = await _ai_chat(
                            [
                                {"role": "system", "content": system45},
                                {
                                    "role": "user",
                                    "content": json.dumps(
                                        l45_input, ensure_ascii=False, indent=1),
                                },
                            ],
                            model="pro",  # 成稿用 pro（本地 qwen3.5）
                            max_tokens=16384,
                            json_mode=False,
                            timeout=600,  # 本地推理慢，加长超时
                            scene="tl45_script",
                        )
                        raw45 = (res45.get("reply") or "").strip()
                        raw45 = re.sub(r"^```(?:markdown)?\s*", "", raw45)
                        raw45 = re.sub(r"\s*```\s*$", "", raw45)
                        seg_marks = raw45.count("###")
                        placeholder_hit = bool(
                            re.search(r"\{[a-z_]+\}|段N\b|{seg|{title", raw45))
                        min_len = (max(1000, len(seg_rows) * 400) if bgm_ambience
                                   else max(1600, len(seg_rows) * 500))
                        hard_min = int(min_len * 0.9)
                        thickness_hit = len(raw45) < hard_min  # 重试后仍薄 → 放行自愈加厚
                        if placeholder_hit or seg_marks < len(seg_rows):
                            q45["errors"].append(
                                f"重试后仍{('占位符未替换' if placeholder_hit else f'漏段 {seg_marks}/{len(seg_rows)}')}"
                            )
                        elif res45.get("finish_reason") == "length":
                            q45["errors"].append("成稿被 max_tokens 截断，请后续增大额度")
                        else:
                            full_script = raw45[:20000]
                            if q45["errors"]:
                                q45["errors"] = []
                    else:
                        q45["errors"].append(
                            f"段落标记 {seg_marks} < 段落数 {len(seg_rows)}（成稿漏段）"
                        )
                elif res45.get("finish_reason") == "length":
                    q45["errors"].append("成稿被 max_tokens 截断，请后续增大额度")
                else:
                    # 主调用返回空（provider 静默失败 ok=0/reply 空）→ 显式记错并重试一次
                    if not raw45 and not retried:
                        retried = True
                        q45["warnings"] = (q45.get("warnings") or []) + [
                            "首次成稿为空（provider 静默失败/无返回），自动重试"]
                        logger.info("L4.5 空成稿重试：reply 为空")
                        res45 = await _ai_chat(
                            [
                                {"role": "system", "content": system45},
                                {
                                    "role": "user",
                                    "content": json.dumps(
                                        l45_input, ensure_ascii=False, indent=1),
                                },
                            ],
                            model="pro",  # 成稿用 pro（本地 qwen3.5）
                            max_tokens=16384,
                            json_mode=False,
                            timeout=600,  # 本地推理慢，加长超时
                            scene="tl45_script",
                        )
                        raw45 = (res45.get("reply") or "").strip()
                        raw45 = re.sub(r"^```(?:markdown)?\s*", "", raw45)
                        raw45 = re.sub(r"\s*```\s*$", "", raw45)
                        seg_marks = raw45.count("###")
                        placeholder_hit = bool(
                            re.search(r"\{[a-z_]+\}|段N\b|{seg|{title", raw45))
                        if not raw45:
                            q45["errors"].append(
                                "L4.5 重试后成稿仍为空（provider 无返回），请检查服务商状态")
                        else:
                            full_script = raw45[:20000]
                            q45["errors"] = []
                    elif not raw45:
                        q45["errors"].append(
                            "L4.5 主调用返回空且重试后仍空，成稿为空（provider 静默失败）")
                    # 多段且未重试过：先压缩重试一次（段落唯一性硬约束）
                    elif seg_marks > len(seg_rows) and not retried:
                        retried = True
                        q45["warnings"] = (q45.get("warnings") or []) + [
                            f"首次成稿拆段（{seg_marks}段>输入{len(seg_rows)}段），自动重试压缩"
                        ]
                        logger.info(
                            "L4.5 多段重试：seg_marks=%s/%s", seg_marks, len(seg_rows))
                        res45 = await _ai_chat(
                            [
                                {"role": "system", "content": system45},
                                {
                                    "role": "user",
                                    "content": json.dumps(
                                        l45_input, ensure_ascii=False, indent=1)
                                    + "\n\n【校验反馈】段落数必须严格等于输入段数（当前拆段过多）。"
                                    "请把内容合并回与输入一致的段落，禁止新增/拆分段落。",
                                },
                            ],
                            model=model,
                            max_tokens=16384,
                            json_mode=False,
                            timeout=300,
                            scene="tl45_script",
                        )
                        raw45 = (res45.get("reply") or "").strip()
                        raw45 = re.sub(r"^```(?:markdown)?\s*", "", raw45)
                        raw45 = re.sub(r"\s*```\s*$", "", raw45)
                        raw45 = re.sub(
                            r"^\s*[-*]?\s*\*\*?差异化硬约束\*\*?[:：]\s*",
                            "补充：", raw45, flags=re.M)
                        raw45 = re.sub(
                            r"\n\s*[-*]?\s*\*\*?差异化硬约束\*\*?[:：]\s*",
                            "\n补充：", raw45)
                        seg_marks = raw45.count("###")
                        placeholder_hit = bool(
                            re.search(r"\{[a-z_]+\}|段N\b|{seg|{title", raw45))
                        if seg_marks > len(seg_rows):
                            q45["warnings"] = q45.get("warnings") or []
                    full_script = raw45[:20000]
                    if q45["errors"]:
                        q45["errors"] = []
                    if seg_marks > len(seg_rows):
                        # 重试后仍多段：告警（内容保留）
                        q45["warnings"] = (q45.get("warnings") or []) + [
                            f"成稿段落数 {seg_marks} 超出输入段数 {len(seg_rows)}"
                            f"（模型私自拆段，结构与 L3 不一致；若拆分合理说明 L3 分段过粗）"
                        ]
                    if len(raw45) < min_len:
                        q45["warnings"] = (q45.get("warnings") or []) + [
                            f"成稿 {len(raw45)} 字略低于目标 {min_len}（10% 容差内放行）"
                        ]
                    if cliche_tail:
                        q45["warnings"] = [f"套话尾句 ×{cliche_tail}（'适用于任何需要…'）"]
                    # harness 创作注解雷同检测：跨段重复句式（编导审稿扣分项）→ warning
                    dup = _detect_annotation_cliches(raw45)
                    if dup or thickness_hit or cliche_tail:
                        q45["warnings"] = (q45.get("warnings") or []) + dup
                        # 自愈：套话/偏薄检测到 → 反馈重写一次（保持结构/句子覆盖不变，只改表达）
                        rewrite_hint = (
                            "你是短视频编导脚本改写师。以下是刚生成的脚本，它被编导审稿判定存在"
                            "套话问题（跨段/跨句模板句式，可套到任何视频）或厚度不足。"
                            "请【重写整个脚本】：\n"
                            "1. 段落结构、段落标题、时间、逐句引用（原话）一律不变；\n"
                            "2. 只改掉套话：每条创作注解/逐句创作意图必须点名本段时间范围内的具体"
                            "画面/字幕/动作/原话字词，且相邻段不得用相同说法；逐句必须保持"
                            "【功能】/【剪辑】/【节奏】三要素结构（剪辑必须点名具体动作："
                            "留白秒数/快切卡点/切黑特写/音效转场）；\n"
                            "3. 全脚本禁止出现这些模板句：'为后续情节…做铺垫'、'为后续情节…做过渡'、"
                            "'增加故事的荒诞性和紧张感'、'吸引观众的注意力'、'激发观众的好奇心'，"
                            "禁止出现'口播结束后的画面段'（段落标题必须写内容标题，如'美妆技巧展示'）、"
                            "'展现…温馨/美好/活力'、'营造…氛围/气氛'、'传递出…情感'、'为观众提供信息'；\n"
                            "4. 审稿指出的套话如下，禁止再次出现（也不要复述本提示要求）：\n"
                            + "\n".join(f"- {d}" for d in dup[:5])
                        )
                        if thickness_hit:
                            rewrite_hint += (
                                f"\n5. 【厚度】当前稿 {len(raw45)} 字不足下限 {min_len}，请加厚到 ≥{min_len} 字："
                                f"每段创作注解 ≥200 字、逐句创作意图 ≥60 字、结尾收束 ≥150 字、"
                                f"纯画面段写满 2~4 条〔画面N〕行且每条 ≥50 字。"
                            )
                        if cliche_tail:
                            rewrite_hint += (
                                f"\n6. 【套话尾句】结尾收束出现 '{cliche_tail} 条'适用于任何需要…'句式，"
                                f"必须改写为点名本片具体手法/选题的安利语（如'这套结构适合美妆圈爆料类选题，"
                                f"用直播互怼开场+母带曝光钩子'）。"
                            )
                        rewrite_hint += "\n\n请直接输出重写后的完整 Markdown 脚本，不要解释。"
                        try:
                            res_rewrite = None
                            for _att in range(3):
                                try:
                                    res_rewrite = await _ai_chat(
                                        [
                                            {"role": "system", "content": system45},
                                            {
                                                "role": "user",
                                                "content": rewrite_hint + "\n\n【当前脚本】\n" + raw45,
                                            },
                                        ],
                                        model=model,
                                        max_tokens=16384,
                                        json_mode=False,
                                        timeout=300,
                                        scene="tl45_rewrite",
                                    )
                                    break
                                except Exception as exc:
                                    if _att == 2:
                                        raise
                                    await asyncio.sleep(5 * (_att + 1))
                                    logger.warning(
                                        "L4.5 套话重写调用失败，重试 %s/3：%s",
                                        _att + 1, str(exc)[:100])
                            rw = (res_rewrite or {}).get("reply") or ""
                            if rw.strip():
                                rw = re.sub(r"^```(?:markdown)?\s*", "", rw.strip())
                                rw = re.sub(r"\s*```\s*$", "", rw)
                                rw = re.sub(
                                    r"^\s*[-*]?\s*\*\*?差异化硬约束\*\*?[:：]\s*",
                                    "补充：", rw, flags=re.M)
                                rw = re.sub(
                                    r"\n\s*[-*]?\s*\*\*?差异化硬约束\*\*?[:：]\s*",
                                    "\n补充：", rw)
                                # 复验：重写后套话是否减少 且 段落数必须仍与 L3 一致
                                # （防模型重写时私自拆/合段，违背段落唯一性）
                                dup2 = _detect_annotation_cliches(rw)
                                rw_marks = rw.count("###")
                                if rw_marks == len(seg_rows) and (
                                    len(dup2) < len(dup) or len(rw) >= hard_min
                                ):
                                    raw45 = rw
                                    full_script = raw45[:20000]  # 重写稿为准
                                    dup = dup2
                                    q45["warnings"] = [
                                        f"套话自愈：检测 {len(dup)} 条 → 自动重写 → 剩余 {len(dup2)} 条"
                                    ] + dup2
                                    if len(raw45) >= hard_min:
                                        q45["warnings"].append(f"自愈后成稿 {len(raw45)} 字达标")
                                elif rw_marks != len(seg_rows):
                                    # 重写破坏了段落结构 → 弃用重写稿，保留原稿（结构正确优先）
                                    q45["warnings"] = q45.get("warnings") or []
                                    q45["warnings"].append(
                                        f"套话自愈重写稿段落数 {rw_marks} ≠ L3 {len(seg_rows)}，"
                                        f"弃用重写稿、保留原稿（段落结构正确优先）"
                                    )
                                else:
                                    # 重写没改善：保留原稿，告警仍在
                                    q45["warnings"] = q45.get("warnings") or []
                        except Exception as exc:
                            logger.warning("L4.5 套话自愈失败：%s", str(exc)[:120])
                        # 局部注解重写：整稿重写未清除"创作注解雷同"时，定点重写雷同段注解。
                        # 整稿重写要改 2000+ 字，flash 常顾此失彼；局部只改 1~2 段注解，精准且成本低。
                        _pairs = set()
                        for _d in dup:
                            for _m in re.finditer(r"段(\d+)\s*与\s*段(\d+)\s*创作注解雷同", _d):
                                _pairs.add(int(_m.group(1)))
                                _pairs.add(int(_m.group(2)))
                        if _pairs and len(_pairs) >= 2:
                            _blocks = re.split(r"(?=### 段)", raw45)
                            _bmap = {}
                            for _b in _blocks:
                                _bm = re.match(r"### 段(\d+)", _b)
                                if _bm:
                                    _bmap[int(_bm.group(1))] = _b
                            _targets = {_n: _bmap[_n] for _n in _pairs if _n in _bmap}
                            if len(_targets) >= 2:
                                _ann = {}
                                for _n, _b in _targets.items():
                                    _am = re.search(
                                        r"(创作注解\s*\*\*?[：:]\s*)(.*?)(?=\n\s*-?\s*\*\*?逐句\*\*?|\n\s*-?\s*〔(?:句|画面)|\Z)",
                                        _b, re.S)
                                    if _am:
                                        _ann[_n] = (_am, _b)
                                if len(_ann) >= 2:
                                    _assets = {
                                        int((_item or {}).get("seq") or 0): "；".join(
                                            str(_x) for _x in ((_item or {}).get("该段画面素材") or []))
                                        for _item in l45_input.get("段落") or []
                                    }
                                    _hint = (
                                        "你是短视频编导脚本的创作注解改写师。以下若干段创作注解被判"
                                        "为雷同（不同段用了相同句式，编导审稿扣分项）。请分别重写：\n"
                                        "1. 每段注解必须点名该段时间范围内的具体画面/字幕/动作/原话字词，"
                                        "不写所有段都成立的空话；\n"
                                        "2. 各段之间不得使用相同句式、相同手法描述、相同情绪走向；\n"
                                        "3. 每段 ≥120 字，保持该段原有的叙事定性（不改变段落内容本身）。\n\n"
                                    )
                                    for _n in sorted(_ann):
                                        _am, _b = _ann[_n]
                                        _hint += (
                                            f"【段{_n}】\n段信息：{_b.splitlines()[0].strip()[:90]}\n"
                                            f"该段画面素材：{_assets.get(_n) or '（输入未提供，请依原注解内容）'}\n"
                                            f"原注解：{re.sub(chr(92)+'s+', ' ', _am.group(2)).strip()}\n\n"
                                        )
                                    _hint += "只输出各段新注解，格式（每段一行，不要其他内容）：\n" + \
                                        "\n".join(f"段{_n}：{{新注解}}" for _n in sorted(_ann))
                                    try:
                                        _res = await _ai_chat(
                                            [{"role": "system", "content": system45},
                                             {"role": "user", "content": _hint}],
                                            model=model, max_tokens=8192, json_mode=False,
                                            timeout=300, scene="tl45_ann_rewrite",
                                        )
                                        _out = ((_res or {}).get("reply") or "").strip()
                                        _new_ann = {}
                                        for _n in sorted(_ann):
                                            _mm = re.search(
                                                rf"段{_n}[：:]\s*(.*?)(?=\n段\d+[：:]|\Z)", _out, re.S)
                                            if _mm:
                                                _txt = _mm.group(1).strip().strip("`").strip()
                                                if len(_txt) >= 80:
                                                    _new_ann[_n] = _txt
                                        if len(_new_ann) >= 2:
                                            _raw_new = raw45
                                            for _n, _txt in _new_ann.items():
                                                _am, _b = _ann[_n]
                                                _new_b = _b[:_am.start(2)] + _txt + _b[_am.end(2):]
                                                _raw_new = _raw_new.replace(_b, _new_b, 1)
                                            _dup3 = _detect_annotation_cliches(_raw_new)
                                            if len(_dup3) < len(dup):
                                                raw45 = _raw_new
                                                full_script = raw45[:20000]
                                                dup = _dup3
                                                q45["warnings"] = [
                                                    f"注解局部重写：段{','.join(str(n) for n in sorted(_new_ann))}"
                                                    f" 已重写 → 剩余 {len(_dup3)} 条"
                                                ] + _dup3
                                            else:
                                                q45["warnings"] = (q45.get("warnings") or []) + [
                                                    "注解局部重写未改善（模型仍用雷同句式），保留原稿"
                                                ]
                                    except Exception as _exc:
                                        logger.warning("L4.5 注解局部重写失败：%s", str(_exc)[:120])
                    # 逐句套话局部定点重写：整稿重写清不掉功能名填空 → 只重写命中句
                    # （每句单独小调用，输入原话+时间+所在段画面素材，输出替换原行）
                    if full_script and (dup or True):
                        _tpl_pats = [
                            r"为后续[^，。]{0,12}(?:剧情|故事|情节|内容)[^，。]{0,12}(?:做铺垫|埋下伏笔|做过渡|奠定基础)",
                            r"为后续情节[^，。]{0,8}做铺垫",
                            r"为后续情节[^，。]{0,8}做过渡",
                            r"(?:实现|自然|完成|从而|以此)(?:过渡|转折)",
                            r"对话[^，。]{0,10}(?:过渡|转向)",
                            r"(?:剧情|故事|情节|内容)[^，。]{0,10}(?:曲折|推进|发展|高潮)",
                            r"(?:再次|进一步)?制造(?:冲突|悬念|紧张感)",
                            r"增加(?:代入感|真实感|层次感)",
                            r"【功能】[^；\n]{0,6}(?:铺垫|过渡|转折|高潮|冲突|悬念|层次|引入|承接)",
                            r"【剪辑】[^；\n]{0,8}(?:正常|常规|平稳|自然)",
                        ]
                        _sent_lines = re.findall(r"〔句\d+[^\n〕〕]*〕[^\n]*", full_script)
                        _dub_q2 = {
                            str(r.quote or "").strip()
                            for r in sentence_rows if _flag_asr_dubious(str(r.quote or ""))
                        }
                        _bad = [
                            ln for ln in _sent_lines
                            if "语音不清" not in ln
                            and not any(
                                re.search(r'〔句\d+[^〕〕]*〕["“]?(' + re.escape(q)[:40] + ')', ln)
                                for q in _dub_q2
                            )
                            and any(re.search(pp, ln) for pp in _tpl_pats)
                        ]
                        if _bad:
                            _fix_map = {}
                            for _ln in _bad[:6]:
                                try:
                                    _m = re.match(r"〔句(\d+)", _ln)
                                    if not _m:
                                        continue
                                    _n = int(_m.group(1))
                                    _sr = next((r for r in sentence_rows if int(r.seq) == _n), None)
                                    if not _sr:
                                        continue
                                    _qtext = getattr(_sr, "quote", None) or getattr(_sr, "text", "")
                                    try:
                                        _sf = [
                                            f for f in frames
                                            if int(f.get("start_ms") or 0) >= int(_sr.start_ms) - 500
                                            and int(f.get("start_ms") or 0) < int(_sr.end_ms) + 500
                                        ]
                                        _vis = "；".join(
                                            dict.fromkeys(str(f.get("desc") or "").strip() for f in _sf[:2])
                                        )[:50] or "（该时段画面素材缺失）"
                                    except (NameError, Exception):
                                        _vis = "（该时段画面素材缺失）"
                                    _hint = (
                                        "你是短视频编导。以下逐句行被审稿判定功能分析是模板填空"
                                        "（'过渡/转折/铺垫/高潮/冲突'这类功能名，可套到任何视频）。"
                                        f"\n原句：{_qtext}\n时间：{_sr.start_ms / 1000:.1f}s~{_sr.end_ms / 1000:.1f}s"
                                        f"\n对应画面：{_vis}\n原逐句行：{_ln}\n"
                                        "要求：只重写【功能】部分（【剪辑】【节奏】保持原样，引用原话字词不变），"
                                        "功能必须写这句在叙事中推动的【具体事件/关系变化】——回答'这句让故事发生了什么'"
                                        "（如'亮底牌——暗示手上有料、准备摊牌，叙事从试探转为对峙'；"
                                        "'制造选择悬念——说还是不说，把观众拉进接下来要爆了的预期'），"
                                        "禁止'过渡/转折/铺垫/层次感/高潮/冲突'功能名。"
                                        "\n直接输出一行新逐句（完整三要素格式，不要解释）："
                                    )
                                    try:
                                        _res = await _ai_chat(
                                            [{"role": "system", "content": system45},
                                             {"role": "user", "content": _hint}],
                                            model=model, max_tokens=2048, json_mode=False,
                                            timeout=180, scene="tl45_sent_rewrite",
                                        )
                                        _out = ((_res or {}).get("reply") or "").strip()
                                        _out = re.sub(r"^```(?:markdown)?\s*", "", _out)
                                        _out = re.sub(r"\s*```\s*$", "", _out)
                                        _oline = re.search(r"〔句\d+[^\n]*", _out)
                                        if not _oline:
                                            logger.warning("L4.5 逐句局部重写响应无句行: %s", _out[:100])
                                            continue
                                        _out = _oline.group(0)
                                        if "【功能】" in _out and "【剪辑】" in _out:
                                            # 新行必须保留原句号与原话（兼容有无引号两种格式）
                                            _oq = re.search(r'〔句\d+[^〕〕]*〕["“]?([^"—\n]{2,64})', _ln)
                                            _nq = re.search(r'〔句\d+[^〕〕]*〕["“]?([^"—\n]{2,64})', _out)
                                            _qq = _oq.group(1).strip().replace(" ", "") if _oq else ""
                                            _nqq = _nq.group(1).strip().replace(" ", "") if _nq else ""
                                            if _oq and _nq and (_qq == _nqq or (_qq and _nqq and (_qq in _nqq or _nqq in _qq))):
                                                # 模型常只输出【功能】→ 缺【剪辑】【节奏】时从原行补回（保证三要素完整）
                                                _oc = re.search(r'【剪辑】[^；\n}]{2,44}', _ln)
                                                _or = re.search(r'【节奏】[^；\n}]{2,20}', _ln)
                                                if "【剪辑】" not in _out and _oc:
                                                    _out = _out.rstrip("；。") + "；" + _oc.group(0)
                                                if "【节奏】" not in _out and _or:
                                                    _out = _out.rstrip("；。") + "；" + _or.group(0)
                                                if "【剪辑】" in _out and "【节奏】" in _out:
                                                    _fix_map[_ln] = _out
                                                else:
                                                    logger.warning(
                                                        "L4.5 逐句局部重写三要素不全: %s", _out[:120])
                                            else:
                                                logger.warning(
                                                    "L4.5 逐句局部重写 quote 不一致: %s", _out[:120])
                                    except Exception as _exc:
                                        logger.warning("L4.5 逐句局部重写失败：%s", str(_exc)[:100])
                                        continue
                                except Exception as _exc2:
                                    logger.warning("L4.5 逐句局部重写失败：%s", str(_exc2)[:100])
                                    continue
                            if _fix_map:
                                _rw2 = full_script
                                for _ln, _nw in _fix_map.items():
                                    _rw2 = _rw2.replace(_ln, _nw, 1)
                                _dup4 = _detect_annotation_cliches(_rw2)
                                if len(_dup4) < len(dup):
                                    full_script = _rw2[:20000]
                                    dup = _dup4
                                    q45["warnings"] = [
                                        f"逐句局部重写：{len(_fix_map)} 句套话已定点改写 → 剩余 {len(_dup4)} 条"
                                    ] + _dup4
                                else:
                                    q45["warnings"] = (q45.get("warnings") or []) + [
                                        f"逐句局部重写未改善 {len(_fix_map)} 句（模型仍写模板），保留原稿"
                                    ]
                    # 最终厚度裁决：自愈重写后仍不足 → 硬 error（编导厚度红线）
                    if full_script and len(full_script) < hard_min:
                        q45["errors"].append(
                            f"成稿最终 {len(full_script)} 字 < 厚度下限 {min_len}（自愈未加厚）")
                    # harness 画面轨：按段落时间精确对齐逐帧简报+动态事件，追加为【画面分镜】
                    # （帧层细节稳定可控；场景记忆层供模型理解叙事，不用于分镜对齐）
                    # 先做段头时间确定性修正：L4.5 段头时间必须以 L3 落库时间为准
                    # （flash 偶发幻觉段头时间，如段2 写 23.2s~50.6s 实为 42s~50.6s，
                    #  时间精确归 harness，语义归模型）
                    for _sg in seg_rows:
                        _pat = rf"(### 段{_sg.seq}[^\n]*?[（(])[\d.]+s~[\d.]+s"
                        _fix = f"{_sg.start_ms / 1000:.1f}s~{_sg.end_ms / 1000:.1f}s"
                        full_script = re.sub(_pat, rf"\g<1>{_fix}", full_script)
                        # 段标题完全没写时间 → harness 确定性追加（时间精确归 harness）
                        _hpat = rf"### 段{_sg.seq}([^\n]*?)(?:[（(][\d.]+s~[\d.]+s[）)])?(?=\n)"
                        def _add_t(_m):
                            _rest = _m.group(1).rstrip()
                            if re.search(r"[（(][\d.]+s~[\d.]+s", _rest):
                                return _m.group(0)
                            return f"### 段{_sg.seq}{_rest}（{_sg.start_ms / 1000:.1f}s~{_sg.end_ms / 1000:.1f}s）"
                        full_script = re.sub(_hpat, _add_t, full_script)
                    # 逐句覆盖补齐：L4.5 偶发漏写部分句子的逐句行（如 7 句只写 4 句）→
                    # 缺失句局部补齐（flash 小调用，三要素格式，插回所属段落逐句区）
                    _present_seqs = {int(m) for m in re.findall(r"〔句(\d+)", full_script)}
                    _all_seqs = {int(r.seq) for r in sentence_rows}
                    _seg_rng = {sg.seq: (sg.start_sentence_seq, sg.end_sentence_seq) for sg in seg_rows}
                    _missing = sorted(x for x in _all_seqs if x not in _present_seqs)
                    if _missing:
                        q45["warnings"].append(f"L4.5 逐句缺失 {_missing}（模型漏写），触发局部补齐")
                        _miss_txt = "\n".join(
                            f"- 〔句{r.seq}〕{r.quote}（{r.start_ms / 1000:.1f}s~{r.end_ms / 1000:.1f}s，句功能：{r.sentence_function}）"
                            for r in sentence_rows if int(r.seq) in _missing
                        )
                        try:
                            from app.ai import chat as _ai_chat  # 延迟导入
                            _resp = await _ai_chat([
                                {"role": "system", "content": (
                                    "你是爆款短视频编导。为缺失的口播句补写逐句分析，严格按格式每句一行："
                                    "〔句N · 强度X〕“原话”——【功能】…；【剪辑】…；【节奏】…。"
                                    "剪辑必须点名具体动作（留白秒数/快切/卡点/切黑/特写/音效/镜头运动），"
                                    "结合本句原话，与已有逐句不雷同。")},
                                {"role": "user", "content": f"缺失句：\n{_miss_txt}\n\n输出：每句一行。"},
                            ], model="pro", timeout=600)  # 补齐用 pro（qwen3.5），格式一致
                            _new_lines = [
                                ln.strip() for ln in ((_resp or {}).get("reply") or "").split("\n")
                                if "〔句" in ln and "【剪辑】" in ln
                            ]
                            if _new_lines:
                                _by_seq: dict[int, list[str]] = {}
                                for _ln in _new_lines:
                                    _mm = re.match(r"〔句(\d+)", _ln)
                                    if _mm:
                                        _by_seq.setdefault(int(_mm.group(1)), []).append(_ln)
                                # 插回所属段逐句区末尾
                                def _ins_missing(_blk: str, _sseq: int) -> str:
                                    _f, _t = _seg_rng.get(_sseq, (0, 0))
                                    _ins = []
                                    for _q in range(_f, _t + 1):
                                        _ins.extend(_by_seq.get(_q, []))
                                    if not _ins:
                                        return _blk
                                    _mi = _blk.find("**逐句**")
                                    if _mi < 0:
                                        return _blk
                                    _nl = _blk.find("\n", _mi)
                                    _tail = _blk[_nl + 1:] if _nl >= 0 else ""
                                    return _blk[: _nl + 1] + "\n".join("  - " + _l for _l in _ins) + "\n" + _tail
                                _parts = re.split(r"(?=### 段\d)", full_script)
                                _out2 = []
                                for _pt in _parts:
                                    _mh = re.match(r"### 段(\d+)", _pt)
                                    if _mh and int(_mh.group(1)) in _seg_rng:
                                        _pt = _ins_missing(_pt, int(_mh.group(1)))
                                    _out2.append(_pt)
                                full_script = "".join(_out2)
                                q45["warnings"].append(f"逐句补齐 {len(_new_lines)} 行（缺失句 {_missing}）")
                            else:
                                q45["warnings"].append("逐句补齐响应无有效行，保留原稿")
                        except Exception as _exc:
                            q45["warnings"].append(f"逐句补齐失败：{str(_exc)[:80]}")
                    # 段标题重复清洗：模型偶发在"该段画面素材"前重复写"### 段N · 标题"行
                    # （段落区已写过一次）→ 删除重复标题行，保留画面素材内容行
                    full_script = re.sub(r"### 段\d+[^\n]*\n(?=该段画面素材)", "", full_script)
                    # 逐句区按段归属过滤：L4.5 偶发把全片逐句重复写入每个段
                    # （段1 写句1-7、段2 也写句1-7）→ 各段逐句区只保留本段行号区间的句子行

                    def _filt_seg_block(blk: str, sseq: int) -> str:
                        _lines = []
                        for _ln in blk.split("\n"):
                            _m = re.search(r"〔句(\d+)", _ln)
                            if _m:
                                _sn = int(_m.group(1))
                                _f, _t = _seg_rng.get(sseq, (0, 0))
                                if not (_f <= _sn <= _t):
                                    continue
                            _lines.append(_ln)
                        return "\n".join(_lines)

                    _parts = re.split(r"(?=### 段\d)", full_script)
                    _out_parts = []
                    for _pt in _parts:
                        _mh = re.match(r"### 段(\d+)", _pt)
                        if _mh and int(_mh.group(1)) in _seg_rng:
                            _pt = _filt_seg_block(_pt, int(_mh.group(1)))
                        _out_parts.append(_pt)
                    full_script = "".join(_out_parts)
                    # 补段标题兜底：L4.5 未重写 harness 补段标题时，确定性替换为"画面收尾"
                    # （补段只出现在口播结束后的片尾画面段，"画面收尾"是时间位置事实，非编造）
                    if "口播结束后的画面段" in full_script:
                        full_script = full_script.replace("口播结束后的画面段", "画面收尾")
                    frames = ((manifest or {}).get("frames")) or []
                    dyns = (((manifest or {}).get("frame_plan") or {}).get("dynamic_events")) or []

                    # 段间真空补段（编导要求全时段覆盖）：相邻段 gap ≥3s（如段1 0~28s → 段2 38~42s
                    # 的 28~38s 空档）→ 成稿插入"节奏缓冲"区，显式说明原因+画面延续+剪辑建议
                    _ts = sorted(seg_rows, key=lambda x: x.seq)
                    if len(_ts) > 1:
                        _ins = []
                        for _a, _b in zip(_ts, _ts[1:]):
                            _gap = _b.start_ms - _a.end_ms
                            if _gap >= 3000:
                                _st, _en = _a.end_ms, _b.start_ms
                                _sf = [
                                    f for f in frames
                                    if int(f.get("start_ms") or 0) >= _st and int(f.get("start_ms") or 0) < _en
                                ]
                                _vis = "；".join(
                                    dict.fromkeys(str(f.get("desc") or "").strip() for f in _sf[:2])
                                )[:60] or "画面延续"
                                _blk = (
                                    f"\n### 节奏缓冲（{_st / 1000:.1f}s~{_en / 1000:.1f}s）\n"
                                    f"- 该区间无口播对话（{(_en - _st) / 1000:.1f}s 空档），画面为：{_vis}；\n"
                                    f"- 建议：保留直播环境音/垫乐作节奏缓冲，或插入关键信息字幕预告"
                                    f"（如'金哥母带曝光'）防止观众流失，避免画面断层感。\n"
                                )
                                _ins.append((_b.seq, _blk))
                        for _bseq, _blk in reversed(_ins):
                            _pp = re.split(rf"(?=### 段{_bseq}[^\n]*)", full_script, maxsplit=1)
                            if len(_pp) == 2:
                                full_script = _pp[0] + _blk + _pp[1]

                    # 动态事件 → 编导语言（harness 确定性翻译，不再是算法原始数据）
                    def _evt_director(e: dict[str, Any]) -> str:
                        _t = int(e.get("t_ms") or 0) / 1000
                        _k = str(e.get("event_type") or "")
                        if _k == "transition":
                            return f"{_t:.0f}s 画面剧烈切换，建议加转场音效（whoosh）或闪白过渡，配合此处情绪转折"
                        if _k == "motion_burst":
                            return f"{_t:.0f}s 画面运动突增，建议卡点加速或插入表情特写，强化节奏记忆点"
                        if _k == "scene_change":
                            return f"{_t:.0f}s 场景切换，建议转场衔接避免跳帧"
                        return f"{_t:.0f}s {_k}（{e.get('note') or ''}）"

                    if frames:
                        vis_lines = ["\n\n## 【画面分镜】（harness 按帧对齐）"]
                        for seg in seg_rows:
                            s0, s1 = seg.start_ms, seg.end_ms
                            seg_frames = [
                                f for f in frames
                                if (f.get("start_ms") is not None and int(f.get("start_ms")) < s1
                                    and int(f.get("end_ms") or f.get("start_ms")) > s0)
                            ]
                            if not seg_frames:
                                continue
                            seen: list[str] = []
                            for f in seg_frames[:4]:
                                t = int(f.get("start_ms") or 0) / 1000
                                desc = (f.get("desc") or "").strip().rstrip("。")[:46]
                                # 编导审稿：分镜不得用主观心理描写（"眼神困惑/好奇"）——
                                # 纯主观帧降级为"面部特写"标记；含视觉元素（标题/字幕/文字/背景）的帧保留
                                if re.search(
                                    r"眼神|表情|困惑|好奇|专注|思考|平静|严肃|微笑|皱眉|惊讶|神态|情绪", desc
                                ) and not re.search(
                                    r"标题|字幕|文字|数字|文本框|评论|背景|大字|沙发|墙壁|白色", desc
                                ):
                                    desc = "面部特写（表情延续，无新增画面信息）"
                                style = f.get("style") or ""
                                item = f"{desc}（{t:.0f}s，{style}）"
                                if item not in seen:
                                    seen.append(item)
                            brief = " → ".join(seen)
                            if len(seg_frames) > 4:
                                brief += "…"
                            overlay = "；".join(
                                dict.fromkeys(
                                    str(f.get("text_overlay") or "").strip()
                                    for f in seg_frames if f.get("text_overlay")
                                )
                            )[:80]
                            line = f"- 段{seg.seq}（{s0 / 1000:.0f}s~{s1 / 1000:.0f}s）：{brief}"
                            if overlay:
                                line += f"；字幕：{overlay}"
                            vis_lines.append(line)
                        dyn_lines = [f"- 动态：{_evt_director(e)}" for e in dyns]
                        if dyn_lines:
                            vis_lines.append("".join(dyn_lines))
                        full_script = full_script + "\n" + "\n".join(vis_lines)
                        # 存疑句成稿强制替换（编导审稿：乱码句不得被逐句分析——harness 确定性，
                        # 不依赖模型自觉）：存疑句的逐句行若未标注"语音不清/无法转写"，
                        # 直接替换为中性记录行（不编造语义、不继续分析乱码文本）
                        _dub_q = {
                            str(r.quote or "")
                            for r in sentence_rows
                            if _flag_asr_dubious(str(r.quote or ""))
                        }
                        if _dub_q:
                            def _fix_dub(m):
                                q = m.group(2).strip().strip('"“”')
                                if q in _dub_q and "语音不清" not in m.group(0) and "无法" not in m.group(0):
                                    return (
                                        f"{m.group(1)}\"{q}\" ——【语音不清】该句转写不准确"
                                        f"（直播杂音/口误），无法作为台词语义拆解；"
                                        f"仅记录其存在与时间位置，作用是为直播间提供真实对话氛围。"
                                    )
                                return m.group(0)
                            full_script = re.sub(
                                r'(〔句\d+[^〕〕]*〕)([^—\n]{2,64})——【[^\n]*',
                                _fix_dub, full_script)
        except Exception as exc:
            q45["errors"].append(str(exc)[:160])
            logger.warning("L4.5 脚本还原失败：%s", exc)
    quality["L4.5"] = q45
    if full_script:
        script.full_script = full_script

    # ---------- L5 积木提炼 ----------
    await report("L5", 80, "三层链路 · L5 积木提炼（六类分库）…")
    l5_user = (
        "【L4 句级脚本（seq / 原话 / 机制 / 手法引用 / 强度）】\n"
        + json.dumps(
            [
                {
                    "seq": r.seq, "quote": r.quote, "function": r.sentence_function,
                    "function_reason": r.function_reason, "method_refs": r.method_refs,
                    "emotion_intensity": r.emotion_intensity,
                }
                for r in sentence_rows
            ],
            ensure_ascii=False,
        )
        + f"\n\n【中心思想】{script.core_idea}\n【内容走向】{script.content_trend}\n【目标人群】{script.target_audience}"
    )
    # 按内容类型分库分批调用：单批输出可控，避免长输出被 provider 5xx 打回
    lib_targets = (
        ("topics", "选题库"), ("hooks", "钩子库"), ("copywriting", "文案库"),
        ("quotes", "金句库"), ("methods", "手法库"),
    )
    data5: dict[str, Any] = {}
    q5_agg: dict[str, Any] = {"arrays": {}, "prompt_version": None, "errors": []}
    for key, label in lib_targets:
        await report("L5", 80, f"三层链路 · L5 积木提炼（{label}）…")
        ok_part, part, q5 = await _run_layer(
            db, video=video, analysis_id=analysis_id, layer=5,
            user_text=l5_user + f"\n\n【本次任务】只输出 {key} 数组（{label}），其它数组一律不要输出。",
            model=model, array_keys=[key], max_tokens=16384,
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
            source_video_id=video.id,
            source_sentence_id=(sent_row.id if sent_row else None),
            source_segment_id=(segment_of_line(int(raw["seq"])).id if segment_of_line(int(raw["seq"])) else None),
            quote=str(raw["text"])[:4000],
            start_ms=int(raw["start_ms"]),
            end_ms=int(raw["end_ms"]),
            source_platform=ctx["platform"][:32] if ctx["platform"] else None,
        )

    async def _upsert_lib(model_cls, code: str, values: dict[str, Any], source_video_id: str | None = None):
        # per-video 隔离：按 (code, source_video_id) upsert
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
        """构建定点深度化的来源上下文：证据链 + 来源句 + 所在段落 + 曲线位置 + 人群。"""
        raw = _line_to_sentence(sentences, item.get("source_line_no"))
        sent = next((r for r in sentence_rows if raw and int(r.seq) == int(raw["seq"])), None)
        seg = segment_of_line(int(raw["seq"])) if raw else None
        dur = int(script.duration_ms or 0) or 1
        return {
            "证据原话": str(getattr(ref, "quote", "") or "")[:400],
            "秒数": f"{int(getattr(ref, 'start_ms', 0) or 0)}ms → {int(getattr(ref, 'end_ms', 0) or 0)}ms"
            f"（占片 {round(int(getattr(ref, 'start_ms', 0) or 0) / dur, 2)}~{round(int(getattr(ref, 'end_ms', 0) or 0) / dur, 2)}）",
            "来源句功能": str(sent.sentence_function if sent else "") or "",
            "来源句情绪强度": str(sent.emotion_intensity if sent else "") or "",
            "所在段落": f"{seg.seg_type if seg else ''}｜{seg.title if seg else ''}" if seg else "",
            "目标人群": str(script.target_audience or "")[:200],
            "中心思想": str(script.core_idea or "")[:200],
        }

    async def _deepen_and_validate(
        table: str, label: str, item: dict[str, Any], values: dict[str, Any], v, ref: M.RefElementSource
    ) -> tuple[dict[str, Any], Any]:
        """R15 深度不足 → 定点深度化（单元素单任务）→ 重新校验；失败保留原判定。"""
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
        logger.info("L5 R15 定点深度化后仍不达标：%s（%s）", label, [s for s in v2.soft if s.startswith("R15")])
        return values, v

    topic_rows: list[M.Any] = []
    if ok5:
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
            refs = []
            tmp = type("_Tmp", (), {"id": None})()
            tmp.id = __import__("uuid").uuid4()
            ref = _ref(item.get("source_line_no"), tmp, "lib_topic")
            if ref is None:
                continue
            v = validator.validate("lib_topic", {**item, **values}, label=code, refs=[{"quote": ref.quote, "start_ms": ref.start_ms, "end_ms": ref.end_ms}])
            accepted, v, values = await _r12_retry_and_validate(
                db, video, analysis_id, model, validator, "lib_topic", code, item, values, v,
                [{"quote": ref.quote, "start_ms": ref.start_ms, "end_ms": ref.end_ms}],
            )
            if not accepted:
                verdicts.append(v.as_dict())
                continue
            values, v = await _deepen_and_validate("lib_topic", code, item, values, v, ref)
            row, _ = await _upsert_lib(M.LibTopic, code, {**values, "quality_score": v.quality_score, "status": "active", "review_status": "accepted" if v.status == "active" else "draft"}, source_video_id=video.id)
            ref.element_id = row.id
            db.add(ref)
            verdicts.append(v.as_dict())
            topic_rows.append(row)
        lib_counts["lib_topic"] = len(topic_rows)

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
            v = validator.validate("lib_hook", {**item, **values}, label=code, refs=[{"quote": ref.quote, "start_ms": ref.start_ms, "end_ms": ref.end_ms}])
            accepted, v, values = await _r12_retry_and_validate(
                db, video, analysis_id, model, validator, "lib_hook", code, item, values, v,
                [{"quote": ref.quote, "start_ms": ref.start_ms, "end_ms": ref.end_ms}],
            )
            if not accepted:
                verdicts.append(v.as_dict())
                continue
            values, v = await _deepen_and_validate("lib_hook", code, item, values, v, ref)
            row, _ = await _upsert_lib(M.LibHook, code, {**values, "quality_score": v.quality_score, "status": "active", "review_status": "accepted" if v.status == "active" else "draft"}, source_video_id=video.id)
            ref.element_id = row.id
            db.add(ref)
            verdicts.append(v.as_dict())
            hook_rows.append(row)
        lib_counts["lib_hook"] = len(hook_rows)

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
            v = validator.validate("lib_copywriting", {**item, **values}, label=code, refs=[{"quote": ref.quote, "start_ms": ref.start_ms, "end_ms": ref.end_ms}])
            accepted, v, values = await _r12_retry_and_validate(
                db, video, analysis_id, model, validator, "lib_copywriting", code, item, values, v,
                [{"quote": ref.quote, "start_ms": ref.start_ms, "end_ms": ref.end_ms}],
            )
            if not accepted:
                verdicts.append(v.as_dict())
                continue
            values, v = await _deepen_and_validate("lib_copywriting", code, item, values, v, ref)
            row, _ = await _upsert_lib(M.LibCopywriting, code, {**values, "quality_score": v.quality_score, "status": "active", "review_status": "accepted" if v.status == "active" else "draft"}, source_video_id=video.id)
            ref.element_id = row.id
            db.add(ref)
            verdicts.append(v.as_dict())
            copy_rows.append(row)
        lib_counts["lib_copywriting"] = len(copy_rows)

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
            v = validator.validate("lib_quote", {**item, **values}, label=code, refs=[{"quote": ref.quote, "start_ms": ref.start_ms, "end_ms": ref.end_ms}])
            accepted, v, values = await _r12_retry_and_validate(
                db, video, analysis_id, model, validator, "lib_quote", code, item, values, v,
                [{"quote": ref.quote, "start_ms": ref.start_ms, "end_ms": ref.end_ms}],
            )
            if not accepted:
                verdicts.append(v.as_dict())
                continue
            values, v = await _deepen_and_validate("lib_quote", code, item, values, v, ref)
            row, _ = await _upsert_lib(M.LibQuote, code, {**values, "quality_score": v.quality_score, "status": "active", "review_status": "accepted" if v.status == "active" else "draft"}, source_video_id=video.id)
            ref.element_id = row.id
            db.add(ref)
            verdicts.append(v.as_dict())
            quote_rows.append(row)
        lib_counts["lib_quote"] = len(quote_rows)

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
            v = validator.validate("lib_method", {**item, **values}, label=code, refs=[{"quote": ref.quote, "start_ms": ref.start_ms, "end_ms": ref.end_ms}])
            accepted, v, values = await _r12_retry_and_validate(
                db, video, analysis_id, model, validator, "lib_method", code, item, values, v,
                [{"quote": ref.quote, "start_ms": ref.start_ms, "end_ms": ref.end_ms}],
            )
            if not accepted:
                verdicts.append(v.as_dict())
                continue
            values, v = await _deepen_and_validate("lib_method", code, item, values, v, ref)
            row, _ = await _upsert_lib(M.LibMethod, code, {**values, "quality_score": v.quality_score, "status": "active", "review_status": "accepted" if v.status == "active" else "draft"}, source_video_id=video.id)
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
                    "seq": s.seq, "seg_type": s.seg_type, "title": s.title,
                    "start_sentence_seq": s.start_sentence_seq, "end_sentence_seq": s.end_sentence_seq,
                    "start_ms": s.start_ms, "end_ms": s.end_ms, "emotion_peak": s.emotion_peak,
                }
                for s in seg_rows
            ],
            ensure_ascii=False,
        )
        + f"\n\n【L5 手法库清单】\n{json.dumps(method_codes, ensure_ascii=False) if method_codes else '（无）'}"
        + f"\n\n【情绪曲线】采样间隔 {interval_ms}ms，形状 {stats['shape']}，峰值落点 {stats['peak_position_ratio']}，"
        + f"峰值 {stats['series_max']}，基线 {stats['baseline_intensity']}"
        + f"\n【中心思想】{script.core_idea}\n【内容走向】{script.content_trend}"
        + "\n要求：槽位与段落一一对应（segment_seq 必须取自上面的 seq），槽位区间首尾相接覆盖 [0,1]。"
    )
    ok6, data6, q6 = await _run_layer(
        db, video=video, analysis_id=analysis_id, layer=6,
        user_text=l6_user, model=model, array_keys=LAYER_ARRAY_KEYS[6], max_tokens=16384,
    )
    quality["L6"] = q6

    combo_data = dict(data6.get("combo") or {})
    slots_json = list(data6.get("slots") or [])
    combo_row = None
    slot_rows: list[M.LibComboSlot] = []
    if combo_data and slots_json:
        # 槽位区间以段落真实时长占比兜底（无缝无重叠由 segment 顺序保证）
        seg_total = sum(max(1, s.end_ms - s.start_ms) for s in seg_rows) or 1
        cursor = 0.0
        fixed = swap = 0
        for i, item in enumerate(sorted(slots_json, key=lambda x: float(x.get("position_ratio_start") or 0)), start=1):
            seg = next((s for s in seg_rows if int(s.seq) == int(item.get("segment_seq") or i)), seg_rows[min(i - 1, len(seg_rows) - 1)])
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
                    combo_id=None,  # flush 后回填
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
        shape = str(combo_data.get("emotion_shape") or stats["shape"])
        if shape not in validator.wl.get("shape", set()):
            shape = stats["shape"]
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
        ref_payload = [{"quote": ref.quote, "start_ms": ref.start_ms, "end_ms": ref.end_ms}] if ref is not None else []
        v = validator.validate("lib_combo", combo_payload, label=code, refs=ref_payload)
        validator.r14_combo(combo_payload, slot_payload, v)
        verdicts.append(v.as_dict())
        if v.accepted:
            combo_row, _ = await _upsert_lib(M.LibCombo, code, {**combo_values, "quality_score": v.quality_score, "status": "active", "review_status": "accepted" if v.status == "active" else "draft"}, source_video_id=video.id)
            if ref is not None:
                ref.element_id = combo_row.id
                db.add(ref)
            # 组合模板按 code 覆盖重写：先清掉该 combo 的旧槽位，避免 (combo_id, seq) 唯一约束冲突
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

    # 汇总
    verdict_list = verdicts
    rejected = [v for v in verdict_list if v.get("status") == "rejected"]
    draft = [v for v in verdict_list if v.get("status") == "draft"]

    # R8 逐字口径统计（与上一版对比的关键证据）：
    # - verbatim：quote 去噪后是原料链（raw_transcript_sentence 按 seq 拼接）的连续子串，
    #   跨句拼接（span > 1）同样计入，因为它仍是 100% 原话；
    # - single_raw：旧口径，quote 只与"锚定的那一条 raw 碎片"比对，跨句拼接必然掉分，
    #   保留该口径仅用于和上一版的 24/26 对账。
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
        # 新口径：quote 是原料链上的连续子串（允许跨句拼接）
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
        # 旧口径（仅供与上一版 24/26 对账）：quote 与锚定单条 raw 碎片的相似度
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
