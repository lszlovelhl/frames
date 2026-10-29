"""节奏曲线推导（纯函数，零 LLM 调用、零 DB 访问）。

口径区分（验收七维中的②节奏曲线 与 ③情绪曲线）：

- **情绪曲线**（``emotion.py`` / ``script_emotion_curve``）：内容情绪强度随时间的起伏，
  取值 0~10，由采样序列拟合得到；
- **节奏曲线**（本模块）：剪辑与信息密度视角——段落时长占比、单位秒句数密度、
  句间停顿转换次数，全部由已落库的 ``script_segment`` / ``script_sentence``
  结构化字段确定性推导，不新增模型调用、不落新表，只写入 ``evidence.rhythm``。
"""
from __future__ import annotations

from typing import Any, Sequence

# ---------------- 节奏判定阈值（魔法数集中处，禁止散落硬编码） ----------------
PAUSE_GAP_MS = 1500          # 相邻口播句间隔 ≥ 该值视为一次节奏转换（停顿切分信号）
SLOW_DENSITY = 0.60          # 句/秒 低于该值 → 节奏偏慢
FAST_DENSITY = 1.10          # 句/秒 高于该值 → 节奏偏快
COVERAGE_OK_RATIO = 0.85     # 段落覆盖的句子占比下限（质量门复用）
MIN_USABLE_SEGMENTS = 1      # 至少 1 个落库段落才认为节奏可推导
DENSITY_ROUND = 3

# 节奏档位标签
PACE_SLOW = "慢"
PACE_MID = "中"
PACE_FAST = "快"
PACE_SILENT = "无口播"


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _pace_label(density: float, sentence_count: int) -> str:
    if sentence_count <= 1:
        return PACE_SILENT
    if density >= FAST_DENSITY:
        return PACE_FAST
    if density <= SLOW_DENSITY:
        return PACE_SLOW
    return PACE_MID


def derive_rhythm(
    segments: Sequence[dict[str, Any]],
    sentences: Sequence[dict[str, Any]],
    *,
    duration_ms: int = 0,
) -> dict[str, Any]:
    """由段落 / 句子结构化数据推导节奏特征。

    参数均为普通 dict（ORM 行可用 ``vars`` 或逐字段取值后传入），保证本函数
    可在离线单测中直接驱动。

    返回 ::

        {
          "derived": True,
          "duration_ms": int,
          "segment_count": int,
          "sentence_count": int,
          "coverage_ratio": float,        # 被段落覆盖的句子占比
          "overall_sentence_density": float,  # 句/秒
          "pace_label": "慢|中|快|无口播",
          "transition_count": int,       # 长停顿转换次数
          "segments": [{seq, seg_type, duration_ms, share,
                        sentence_count, density}],
          "usable": bool,
          "note": str,
        }
    """
    total_ms = max(1, int(duration_ms or 0))
    seg_list = [dict(s) for s in (segments or [])]
    sent_list = [dict(s) for s in (sentences or [])]

    seq_index: dict[int, dict[str, Any]] = {}
    for s in sent_list:
        seq = _as_int(s.get("seq"))
        if seq:
            seq_index[seq] = s

    covered: set[int] = set()
    seg_stats: list[dict[str, Any]] = []
    for seg in seg_list:
        s0 = _as_int(seg.get("start_ms"))
        s1 = _as_int(seg.get("end_ms"))
        span_ms = max(1, s1 - s0)
        f_seq = _as_int(seg.get("start_sentence_seq"))
        t_seq = _as_int(seg.get("end_sentence_seq"))
        cnt = 0
        if f_seq and t_seq and f_seq <= t_seq:
            for seq in range(f_seq, t_seq + 1):
                if seq in seq_index:
                    covered.add(seq)
                    cnt += 1
        elif f_seq and f_seq in seq_index:
            covered.add(f_seq)
            cnt = 1
        density = round(cnt / max(1.0, span_ms / 1000.0), DENSITY_ROUND)
        seg_stats.append(
            {
                "seq": _as_int(seg.get("seq"), len(seg_stats) + 1),
                "seg_type": str(seg.get("seg_type") or ""),
                "duration_ms": span_ms,
                "share": round(span_ms / total_ms, 4),
                "sentence_count": cnt,
                "density": density,
                "hook_point": bool(seg.get("hook_point")),
                "payoff_point": bool(seg.get("payoff_point")),
            }
        )

    total_sent = len(sent_list)
    coverage_ratio = round(len(covered) / max(1, total_sent), 4) if sent_list else 0.0
    duration_s = total_ms / 1000.0
    overall_density = round(total_sent / max(1.0, duration_s), DENSITY_ROUND)
    pace = _pace_label(overall_density, total_sent)

    # 句间长停顿 → 节奏转换次数（与 stage3 的 1500ms 停顿兜底口径一致）
    ordered = sorted(sent_list, key=lambda s: _as_int(s.get("start_ms")))
    transition_count = 0
    prev_end = None
    for s in ordered:
        start = _as_int(s.get("start_ms"))
        if prev_end is not None and start - prev_end >= PAUSE_GAP_MS:
            transition_count += 1
        end = _as_int(s.get("end_ms"), start)
        prev_end = max(prev_end or start, end)

    usable = (
        len(seg_list) >= MIN_USABLE_SEGMENTS
        and coverage_ratio >= COVERAGE_OK_RATIO
        and total_sent >= 1
    )
    note = (
        f"段落 {len(seg_list)} 个、覆盖句子 {len(covered)}/{total_sent}"
        f"（{coverage_ratio:.0%}），整体密度 {overall_density} 句/秒（{pace}），"
        f"停顿转换 {transition_count} 次"
    )
    return {
        "derived": True,
        "duration_ms": total_ms,
        "segment_count": len(seg_list),
        "sentence_count": total_sent,
        "coverage_ratio": coverage_ratio,
        "overall_sentence_density": overall_density,
        "pace_label": pace,
        "transition_count": transition_count,
        "segments": seg_stats,
        "usable": usable,
        "note": note,
    }
