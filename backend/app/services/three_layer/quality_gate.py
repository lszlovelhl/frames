"""拆解质量门（纯函数，零 LLM 调用、零 DB 访问）。

在三层链路收尾处对结构化产物做一次性体检，输出 ::

    {
      "passed": bool,
      "score": float,           # 加权 0~100
      "checks": [ {key,label,weight,passed,detail} ... ],
      "issues": [str, ...],     # 不合格项人话描述
      "fixes":  [str, ...],     # 对应可执行修订建议
      "critical_failed": [str, ...],   # 关键项不过则直接判 False（不论总分）
      "thresholds": {...},      # 本次使用的阈值快照，便于排障
    }

设计原则：
- 只吃已落库 / 已汇总的结构化数据（dict 列表 + evidence 标量），离线可测；
- 阈值与权重全部提为模块常量，禁止在调用点散落魔法数；
- 不做任何写库动作，写回 ``job.evidence["quality_gate"]`` 由 pipeline 负责。
"""
from __future__ import annotations

from typing import Any

# ---------------- 阈值与权重（常量集中处） ----------------
PASS_SCORE = 70.0                 # 加权总分及格线
MIN_CORE_IDEA_LEN = 8             # 核心思想最短字数
MIN_SUMMARY_LEN = 30              # 摘要最短字数
MIN_HOOKS = 1                     # 至少识别 1 个钩子
MIN_VERBATIM_RATE = 0.60          # 原话锚定率下限（复用 quote_verbatim_rate）
MIN_SEGMENT_COVERAGE = 0.85       # 段落覆盖句子占比下限
MIN_REUSABLE_ELEMENTS = 1         # 可复用积木元素最少个数
MAX_INTERP_RATIO = 0.30           # 情绪曲线插值点占比上限（与 stage2 口径一致）
CURVE_MIN_RANGE = 1.5             # 情绪曲线强度极差下限（低于则判平直退化）

# 七项检查权重（合计 1.0）
WEIGHTS: dict[str, float] = {
    "core_idea": 0.15,
    "hook_present": 0.15,
    "emotion_curve": 0.15,
    "rhythm_curve": 0.10,
    "verbatim_anchor": 0.15,
    "segment_coverage": 0.15,
    "reusable_elements": 0.15,
}

# 关键项：任一不过即整体不通过（防止总分被宽松项拉过线）
CRITICAL_KEYS = ("core_idea", "hook_present", "verbatim_anchor", "segment_coverage")

_CHECK_LABELS = {
    "core_idea": "核心思想与摘要非空且达标",
    "hook_present": "至少识别 1 个开场钩子",
    "emotion_curve": "情绪曲线可用（非平直、插值占比受控）",
    "rhythm_curve": "节奏曲线可推导（段落覆盖充分）",
    "verbatim_anchor": "原话锚定率达标",
    "segment_coverage": "段落覆盖全片句子",
    "reusable_elements": "可复用积木元素非空",
}


def _missing_placeholder(text: Any) -> bool:
    s = str(text or "").strip()
    return not s or s.startswith("（缺失") or s == "缺失"


def _check_core_idea(payload: dict[str, Any]) -> tuple[bool, str]:
    idea = str(payload.get("core_idea") or "")
    summary = str(payload.get("summary") or "")
    if _missing_placeholder(idea):
        return False, "核心思想为空或占位符（（缺失）），定调失败"
    if len(idea.strip()) < MIN_CORE_IDEA_LEN:
        return False, f"核心思想仅 {len(idea.strip())} 字，少于下限 {MIN_CORE_IDEA_LEN} 字"
    if _missing_placeholder(summary) or len(summary.strip()) < MIN_SUMMARY_LEN:
        return False, f"摘要过短或缺失（<{MIN_SUMMARY_LEN} 字），无法支撑复盘"
    return True, f"核心思想 {len(idea.strip())} 字、摘要 {len(summary.strip())} 字，均达标"


def _check_hook(payload: dict[str, Any]) -> tuple[bool, str]:
    n = int(payload.get("hooks_count") or 0)
    if n < MIN_HOOKS:
        return False, "未识别到任何钩子（段落 hook_point / 句级 is_hook / 钩子库均为空）"
    return True, f"识别钩子 {n} 处"


def _check_emotion_curve(payload: dict[str, Any]) -> tuple[bool, str]:
    curve = payload.get("curve") or {}
    if not curve:
        return False, "情绪曲线缺失（L2 未产出）"
    if curve.get("degenerate"):
        return False, "情绪曲线近乎平直（峰谷区分度不足，见 warnings）"
    interp = float(curve.get("interpolated_ratio") or 0.0)
    if interp > MAX_INTERP_RATIO:
        return False, f"曲线插值点占比 {interp:.0%} 超过上限 {MAX_INTERP_RATIO:.0%}"
    series_max = curve.get("series_max")
    series_min = curve.get("series_min")
    if series_max is not None and series_min is not None:
        if float(series_max) - float(series_min) < CURVE_MIN_RANGE:
            return False, (
                f"情绪强度极差 {float(series_max) - float(series_min):.1f} "
                f"< {CURVE_MIN_RANGE}，曲线退化"
            )
    return True, (
        f"曲线形状 {curve.get('shape') or '未知'}、峰 {curve.get('peak_count', 0)} 个、"
        f"插值占比 {interp:.0%}，可用"
    )


def _check_rhythm(payload: dict[str, Any]) -> tuple[bool, str]:
    rhythm = payload.get("rhythm") or {}
    if not rhythm:
        return False, "节奏特征未推导（无段落/句子数据）"
    if not bool(rhythm.get("usable")):
        return False, (
            f"节奏不可用：段落 {rhythm.get('segment_count', 0)} 个、"
            f"覆盖 {rhythm.get('coverage_ratio', 0):.0%}"
        )
    return True, (
        f"节奏 {rhythm.get('pace_label', '未知')}、密度 "
        f"{rhythm.get('overall_sentence_density', 0)} 句/秒、"
        f"覆盖 {rhythm.get('coverage_ratio', 0):.0%}"
    )


def _check_verbatim(payload: dict[str, Any]) -> tuple[bool, str]:
    rate = float(payload.get("quote_verbatim_rate") or 0.0)
    if rate < MIN_VERBATIM_RATE:
        return False, f"原话锚定率 {rate:.0%} 低于下限 {MIN_VERBATIM_RATE:.0%}（存在改写/杜撰风险）"
    return True, f"原话锚定率 {rate:.0%}，达标"


def _check_segment_coverage(payload: dict[str, Any]) -> tuple[bool, str]:
    ratio = float(payload.get("segment_coverage") or 0.0)
    if ratio < MIN_SEGMENT_COVERAGE:
        return False, f"段落仅覆盖 {ratio:.0%} 的句子（下限 {MIN_SEGMENT_COVERAGE:.0%}），存在漏拆"
    return True, f"段落覆盖 {ratio:.0%} 的句子"


def _check_reusable_elements(payload: dict[str, Any]) -> tuple[bool, str]:
    lib_counts = payload.get("lib_counts") or {}
    total = sum(int(v or 0) for v in lib_counts.values())
    baseline = payload.get("element_baseline") or {}
    if total < MIN_REUSABLE_ELEMENTS:
        return False, "可复用积木元素为空（L5 钩子/手法/选题等均未落库）"
    if baseline and baseline.get("pass") is False:
        return False, f"元素基线未通过：{baseline.get('reason', '')}"
    return True, f"可复用元素合计 {total} 条"


_CHECKS = (
    ("core_idea", _check_core_idea),
    ("hook_present", _check_hook),
    ("emotion_curve", _check_emotion_curve),
    ("rhythm_curve", _check_rhythm),
    ("verbatim_anchor", _check_verbatim),
    ("segment_coverage", _check_segment_coverage),
    ("reusable_elements", _check_reusable_elements),
)

# 不合格项 → 修订建议（供闭环与人工复核使用）
_FIX_HINTS = {
    "core_idea": "重写定调：围绕 key_facts 真实人物/事件补全核心思想，并把摘要扩写到 30 字以上",
    "hook_present": "回看前 3 秒台词与画面，补标开场钩子（句级 is_hook 或段落 hook_point）",
    "emotion_curve": "检查音频能量/句子强度采样；插值占比过高时补充模型情绪点，避免平直曲线",
    "rhythm_curve": "按口播停顿与话题转换重切段落，消除漏覆盖句子",
    "verbatim_anchor": "逐句对齐 #03 转写原话，删除模型改写/杜撰的 quote，保证逐字一致",
    "segment_coverage": "把未覆盖句子并入相邻段落，保证段落首尾相接覆盖全片",
    "reusable_elements": "补充钩子/手法/选题等积木，并为每条补齐机制、变体与发散想象",
}


def evaluate_quality_gate(payload: dict[str, Any]) -> dict[str, Any]:
    """执行七项检查，返回结构化判定结果。

    payload 字段 ::

        core_idea, summary: str
        hooks_count: int
        curve: dict | None          # quality["curve"]
        rhythm: dict | None         # rhythm.derive_rhythm 输出
        quote_verbatim_rate: float
        segment_coverage: float
        lib_counts: dict
        element_baseline: dict | None
    """
    checks: list[dict[str, Any]] = []
    issues: list[str] = []
    fixes: list[str] = []
    critical_failed: list[str] = []
    weighted = 0.0

    for key, fn in _CHECKS:
        ok, detail = fn(payload)
        weight = WEIGHTS[key]
        weighted += weight * (100.0 if ok else 0.0)
        checks.append(
            {
                "key": key,
                "label": _CHECK_LABELS[key],
                "weight": weight,
                "passed": ok,
                "detail": detail,
            }
        )
        if not ok:
            issues.append(f"[{_CHECK_LABELS[key]}] {detail}")
            fixes.append(_FIX_HINTS[key])
            if key in CRITICAL_KEYS:
                critical_failed.append(key)

    score = round(weighted, 1)
    passed = score >= PASS_SCORE and not critical_failed
    return {
        "passed": passed,
        "score": score,
        "pass_threshold": PASS_SCORE,
        "checks": checks,
        "issues": issues,
        "fixes": fixes,
        "critical_failed": critical_failed,
        "thresholds": {
            "min_core_idea_len": MIN_CORE_IDEA_LEN,
            "min_summary_len": MIN_SUMMARY_LEN,
            "min_hooks": MIN_HOOKS,
            "min_verbatim_rate": MIN_VERBATIM_RATE,
            "min_segment_coverage": MIN_SEGMENT_COVERAGE,
            "min_reusable_elements": MIN_REUSABLE_ELEMENTS,
            "max_interp_ratio": MAX_INTERP_RATIO,
            "curve_min_range": CURVE_MIN_RANGE,
        },
    }
