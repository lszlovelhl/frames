"""完播驱动要素推导（纯函数，零 LLM 调用、零 DB 访问）。

「完播驱动要素」是七维结构中历史上最薄弱的一维：不新建重表，而是由
Stage3 段落标记（``hook_point`` / ``payoff_point`` / ``seg_type``）、
Stage4 句子标记（``is_hook`` / ``is_turn`` / ``is_peak`` / ``sentence_function``）
确定性推导成结构化列表，写入 ``evidence.completion_drivers`` 供质量门与前端消费。

每条驱动 ::

    {
      "driver": "hook_open|curiosity_gap|climax_payoff|value_density|cta_close",
      "label": 中文标签,
      "evidence_quote": "锚定原话（取句子 quote，段落级取段标题/摘要）",
      "t_ms": int,
      "strength": 0.0~1.0,
      "source": "sentence|segment|lib_hook",
    }
"""
from __future__ import annotations

from typing import Any, Sequence

# ---------------- 驱动类型目录（含权重，供质量门/汇总复用） ----------------
DRIVER_HOOK_OPEN = "hook_open"
DRIVER_CURIOUSITY_GAP = "curiosity_gap"
DRIVER_CLIMAX_PAYOFF = "climax_payoff"
DRIVER_VALUE_DENSITY = "value_density"
DRIVER_CTA_CLOSE = "cta_close"

DRIVER_CATALOG: dict[str, dict[str, Any]] = {
    DRIVER_HOOK_OPEN: {"label": "开场钩子", "weight": 0.25},
    DRIVER_CURIOUSITY_GAP: {"label": "悬念/转折缺口", "weight": 0.20},
    DRIVER_CLIMAX_PAYOFF: {"label": "高潮兑现", "weight": 0.20},
    DRIVER_VALUE_DENSITY: {"label": "干货密度", "weight": 0.20},
    DRIVER_CTA_CLOSE: {"label": "行动号召/收尾", "weight": 0.15},
}

MAX_DRIVERS = 12          # evidence 体积上限，超出按强度截断
BUCKET_MS = 1000          # 同一秒内同类型驱动去重
HOOK_POSITION_RATIO = 0.20  # 前 20% 时长内的开场钩子强度更高
END_POSITION_RATIO = 0.85   # 最后 15% 视为收尾区


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clamp(v: float) -> float:
    return round(max(0.0, min(1.0, float(v))), 2)


def _intensity(item: dict[str, Any]) -> float:
    try:
        return max(0.0, min(10.0, float(item.get("emotion_intensity") or 0.0)))
    except (TypeError, ValueError):
        return 0.0


def derive_completion_drivers(
    segments: Sequence[dict[str, Any]],
    sentences: Sequence[dict[str, Any]],
    *,
    duration_ms: int = 0,
    hook_lib_count: int = 0,
) -> list[dict[str, Any]]:
    """汇总句子级与段落级信号，输出完播驱动列表（按时间升序，强度去重截断）。"""
    total_ms = max(1, int(duration_ms or 0))
    entries: list[dict[str, Any]] = []

    # ---- Stage4 句子级信号 ----
    for s in (sentences or []):
        t_ms = _as_int(s.get("start_ms"))
        ratio = t_ms / total_ms
        quote = str(s.get("quote") or "").strip()
        func = str(s.get("sentence_function") or "").strip()
        intensity = _intensity(s)

        if bool(s.get("is_hook")) or func == "钩子":
            strength = 0.85 if ratio <= HOOK_POSITION_RATIO else 0.60
            entries.append(
                {
                    "driver": DRIVER_HOOK_OPEN,
                    "label": DRIVER_CATALOG[DRIVER_HOOK_OPEN]["label"],
                    "evidence_quote": quote[:120],
                    "t_ms": t_ms,
                    "strength": _clamp(strength),
                    "source": "sentence",
                }
            )
        if bool(s.get("is_turn")) or func == "转折":
            entries.append(
                {
                    "driver": DRIVER_CURIOUSITY_GAP,
                    "label": DRIVER_CATALOG[DRIVER_CURIOUSITY_GAP]["label"],
                    "evidence_quote": quote[:120],
                    "t_ms": t_ms,
                    "strength": _clamp(0.70 + intensity / 10.0 * 0.30),
                    "source": "sentence",
                }
            )
        if bool(s.get("is_peak")) or func == "高潮":
            entries.append(
                {
                    "driver": DRIVER_CLIMAX_PAYOFF,
                    "label": DRIVER_CATALOG[DRIVER_CLIMAX_PAYOFF]["label"],
                    "evidence_quote": quote[:120],
                    "t_ms": t_ms,
                    "strength": _clamp(0.55 + intensity / 10.0 * 0.45),
                    "source": "sentence",
                }
            )
        if func == "干货":
            entries.append(
                {
                    "driver": DRIVER_VALUE_DENSITY,
                    "label": DRIVER_CATALOG[DRIVER_VALUE_DENSITY]["label"],
                    "evidence_quote": quote[:120],
                    "t_ms": t_ms,
                    "strength": _clamp(0.62 + intensity / 10.0 * 0.18),
                    "source": "sentence",
                }
            )
        if func in ("CTA", "收尾"):
            near_end = ratio >= END_POSITION_RATIO
            entries.append(
                {
                    "driver": DRIVER_CTA_CLOSE,
                    "label": DRIVER_CATALOG[DRIVER_CTA_CLOSE]["label"],
                    "evidence_quote": quote[:120],
                    "t_ms": t_ms,
                    "strength": _clamp(0.72 if near_end else 0.50),
                    "source": "sentence",
                }
            )

    # ---- Stage3 段落级信号（句子标记缺失时的兜底信号源） ----
    for seg in (segments or []):
        t_ms = _as_int(seg.get("start_ms"))
        seg_type = str(seg.get("seg_type") or "").strip()
        quote = str(seg.get("title") or seg.get("summary") or "").strip()[:120]
        if bool(seg.get("hook_point")) or seg_type == "钩子":
            entries.append(
                {
                    "driver": DRIVER_HOOK_OPEN,
                    "label": DRIVER_CATALOG[DRIVER_HOOK_OPEN]["label"],
                    "evidence_quote": quote or "段落级开场钩子",
                    "t_ms": t_ms,
                    "strength": _clamp(0.70),
                    "source": "segment",
                }
            )
        if bool(seg.get("payoff_point")) or seg_type == "高潮":
            entries.append(
                {
                    "driver": DRIVER_CLIMAX_PAYOFF,
                    "label": DRIVER_CATALOG[DRIVER_CLIMAX_PAYOFF]["label"],
                    "evidence_quote": quote or "段落级兑现点",
                    "t_ms": t_ms,
                    "strength": _clamp(0.75),
                    "source": "segment",
                }
            )
        if seg_type == "CTA":
            entries.append(
                {
                    "driver": DRIVER_CTA_CLOSE,
                    "label": DRIVER_CATALOG[DRIVER_CTA_CLOSE]["label"],
                    "evidence_quote": quote or "段落级行动号召",
                    "t_ms": t_ms,
                    "strength": _clamp(0.68),
                    "source": "segment",
                }
            )
        if seg_type == "干货":
            entries.append(
                {
                    "driver": DRIVER_VALUE_DENSITY,
                    "label": DRIVER_CATALOG[DRIVER_VALUE_DENSITY]["label"],
                    "evidence_quote": quote or "段落级干货区",
                    "t_ms": t_ms,
                    "strength": _clamp(0.60),
                    "source": "segment",
                }
            )

    # ---- 同类型 × 同秒桶去重（保留最强） ----
    best: dict[tuple[str, int], dict[str, Any]] = {}
    for e in entries:
        key = (e["driver"], e["t_ms"] // BUCKET_MS)
        old = best.get(key)
        if old is None or e["strength"] > old["strength"]:
            best[key] = e
    out = sorted(best.values(), key=lambda x: (x["t_ms"], -x["strength"]))
    return out[:MAX_DRIVERS]


def summarize_completion_drivers(
    drivers: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """汇总：类型计数 + 最强驱动，供质量门与看板复用。"""
    by_type: dict[str, int] = {}
    strongest: dict[str, Any] | None = None
    for d in drivers or []:
        by_type[d["driver"]] = by_type.get(d["driver"], 0) + 1
        if strongest is None or d["strength"] > strongest["strength"]:
            strongest = d
    return {
        "count": len(drivers or []),
        "by_type": by_type,
        "types_covered": len(by_type),
        "strongest": strongest,
        "has_hook_open": DRIVER_HOOK_OPEN in by_type,
        "has_cta_close": DRIVER_CTA_CLOSE in by_type,
    }
