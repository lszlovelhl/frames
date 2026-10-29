"""七维结构化输出映射推导（纯函数，零 LLM 调用、零 DB 访问）。

目标七维：①钩子开头 ②节奏曲线 ③情绪曲线 ④镜头/画面语言 ⑤文案结构
⑥完播驱动要素 ⑦可复用创作点。

本模块把分散在各表 / evidence 的结构化字段归并成一张统一画像，
便于：
- 质量门横向校验七维是否齐备；
- 前端/报告直接按维度取数，不再跨表拼装；
- 离线回归测试证明「七维都能从结构化数据推导出来」。

状态口径：``ok`` 数据齐备且达标；``weak`` 有数据但单薄（仅部分信号）；
``missing`` 无数据支撑。
"""
from __future__ import annotations

from typing import Any, Sequence

from .completion import summarize_completion_drivers

# 七维固定键序（对外契约，勿随意改名）
DIMENSION_KEYS: tuple[str, ...] = (
    "hook_open",            # ① 钩子开头
    "rhythm_curve",         # ② 节奏曲线
    "emotion_curve",        # ③ 情绪曲线
    "visual_language",      # ④ 镜头/画面语言
    "copy_structure",       # ⑤ 文案结构
    "completion_drivers",   # ⑥ 完播驱动要素
    "reusable_elements",    # ⑦ 可复用创作点
)

DIMENSION_LABELS = {
    "hook_open": "①钩子开头",
    "rhythm_curve": "②节奏曲线",
    "emotion_curve": "③情绪曲线",
    "visual_language": "④镜头/画面语言",
    "copy_structure": "⑤文案结构",
    "completion_drivers": "⑥完播驱动要素",
    "reusable_elements": "⑦可复用创作点",
}

STATUS_OK = "ok"
STATUS_WEAK = "weak"
STATUS_MISSING = "missing"


def _dim(status: str, source: str, summary: str, count: int = 0, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"status": status, "source": source, "summary": summary, "count": count}
    out.update(extra)
    return out


def assemble_dimensions(
    *,
    core_idea: str = "",
    curve: dict[str, Any] | None = None,
    rhythm: dict[str, Any] | None = None,
    drivers: Sequence[dict[str, Any]] | None = None,
    segments: Sequence[dict[str, Any]] | None = None,
    sentences: Sequence[dict[str, Any]] | None = None,
    lib_counts: dict[str, int] | None = None,
    element_baseline: dict[str, Any] | None = None,
    shots_n: int = 0,
    frame_evidence_n: int = 0,
) -> dict[str, Any]:
    """把各阶段结构化产物归并为七维画像。

    参数均为 dict / 标量 ORM 快照，离线可测；返回 ``{key: dim_dict}`` + 汇总计数。
    """
    segments = list(segments or [])
    sentences = list(sentences or [])
    drivers = list(drivers or [])
    lib_counts = dict(lib_counts or {})
    curve = curve or {}
    rhythm = rhythm or {}
    dims: dict[str, Any] = {}

    # ① 钩子开头：段落 hook_point + 句级 is_hook + 钩子库
    hook_seg = sum(1 for s in segments if s.get("hook_point"))
    hook_sent = sum(1 for s in sentences if s.get("is_hook"))
    hook_lib = int(lib_counts.get("lib_hook", 0))
    hooks_total = hook_seg + hook_sent + hook_lib
    if hooks_total >= 1:
        dims["hook_open"] = _dim(
            STATUS_OK,
            "script_segment.hook_point / script_sentence.is_hook / lib_hook",
            f"开场钩子 {hooks_total} 处（段落 {hook_seg} / 句级 {hook_sent} / 钩子库 {hook_lib}）",
            hooks_total,
            segment_hooks=hook_seg,
            sentence_hooks=hook_sent,
            lib_hooks=hook_lib,
        )
    else:
        dims["hook_open"] = _dim(
            STATUS_MISSING, "script_segment / script_sentence / lib_hook", "未识别到开场钩子", 0
        )

    # ② 节奏曲线：rhythm.py 纯推导
    if rhythm and rhythm.get("usable"):
        dims["rhythm_curve"] = _dim(
            STATUS_OK,
            "evidence.rhythm（段落时长占比 + 句密度推导）",
            rhythm.get("note", "节奏特征已推导"),
            rhythm.get("segment_count", 0),
            pace_label=rhythm.get("pace_label"),
            density=rhythm.get("overall_sentence_density"),
            coverage_ratio=rhythm.get("coverage_ratio"),
            transition_count=rhythm.get("transition_count"),
        )
    elif rhythm:
        dims["rhythm_curve"] = _dim(
            STATUS_WEAK,
            "evidence.rhythm",
            f"节奏数据单薄：{rhythm.get('note', '覆盖不足')}",
            rhythm.get("segment_count", 0),
            pace_label=rhythm.get("pace_label"),
        )
    else:
        dims["rhythm_curve"] = _dim(STATUS_MISSING, "evidence.rhythm", "节奏特征缺失", 0)

    # ③ 情绪曲线：script_emotion_curve 统计
    if curve and (curve.get("shape") or curve.get("sample_count")):
        if curve.get("degenerate"):
            dims["emotion_curve"] = _dim(
                STATUS_WEAK,
                "script_emotion_curve",
                f"曲线形状 {curve.get('shape')} 但偏平直（退化标记）",
                int(curve.get("peak_count") or 0),
                shape=curve.get("shape"),
                peak_count=curve.get("peak_count"),
            )
        else:
            dims["emotion_curve"] = _dim(
                STATUS_OK,
                "script_emotion_curve",
                f"形状 {curve.get('shape')}、峰 {curve.get('peak_count', 0)} 个、"
                f"方法 {curve.get('method')}",
                int(curve.get("peak_count") or 0),
                shape=curve.get("shape"),
                peak_count=curve.get("peak_count"),
                valley_count=curve.get("valley_count"),
                peak_position_ratio=curve.get("peak_position_ratio"),
                method=curve.get("method"),
            )
    else:
        dims["emotion_curve"] = _dim(STATUS_MISSING, "script_emotion_curve", "情绪曲线缺失", 0)

    # ④ 镜头/画面语言：关键帧描述 + frame/ocr 类证据（历史薄弱维：无独立分析表）
    if shots_n > 0 or frame_evidence_n > 0:
        status = STATUS_OK if frame_evidence_n > 0 else STATUS_WEAK
        dims["visual_language"] = _dim(
            status,
            "raw_shot.desc / script_evidence(evidence_type=frame|ocr)",
            f"关键帧 {shots_n} 张、画面类证据 {frame_evidence_n} 条",
            shots_n,
            shots_n=shots_n,
            frame_evidence_n=frame_evidence_n,
            limitation="无独立镜头语言分析表，当前依赖帧描述与证据类型计数",
        )
    else:
        dims["visual_language"] = _dim(
            STATUS_MISSING, "raw_shot / script_evidence", "无可用画面素材与画面证据", 0
        )

    # ⑤ 文案结构：段落类型序列 + 句子功能分布
    type_seq = [str(s.get("seg_type") or "") for s in segments]
    func_dist: dict[str, int] = {}
    for s in sentences:
        f = str(s.get("sentence_function") or "")
        if f:
            func_dist[f] = func_dist.get(f, 0) + 1
    if len(segments) >= 2 and len(sentences) >= 5:
        dims["copy_structure"] = _dim(
            STATUS_OK,
            "script_segment.seg_type 序列 + script_sentence.sentence_function 分布",
            f"{len(segments)} 段（{'→'.join(t for t in type_seq if t)}），句子功能分布 {func_dist}",
            len(segments),
            segment_types=type_seq,
            function_distribution=func_dist,
        )
    elif segments:
        dims["copy_structure"] = _dim(
            STATUS_WEAK,
            "script_segment / script_sentence",
            f"仅 {len(segments)} 个段落 / {len(sentences)} 句，结构颗粒度粗",
            len(segments),
            segment_types=type_seq,
        )
    else:
        dims["copy_structure"] = _dim(STATUS_MISSING, "script_segment", "无段落结构", 0)

    # ⑥ 完播驱动要素：completion.py 推导
    summary = summarize_completion_drivers(drivers)
    if summary["count"] >= 3 and summary["types_covered"] >= 3:
        dims["completion_drivers"] = _dim(
            STATUS_OK,
            "evidence.completion_drivers（段落/句子信号推导）",
            f"驱动要素 {summary['count']} 条、覆盖 {summary['types_covered']} 类",
            summary["count"],
            by_type=summary["by_type"],
            strongest=summary["strongest"],
        )
    elif summary["count"] >= 1:
        dims["completion_drivers"] = _dim(
            STATUS_WEAK,
            "evidence.completion_drivers",
            f"驱动要素仅 {summary['count']} 条、覆盖 {summary['types_covered']} 类",
            summary["count"],
            by_type=summary["by_type"],
        )
    else:
        dims["completion_drivers"] = _dim(
            STATUS_MISSING, "evidence.completion_drivers", "未推导出任完播驱动要素", 0
        )

    # ⑦ 可复用创作点：六张 lib_* 表计数 + 元素基线
    total_lib = sum(int(v or 0) for v in lib_counts.values())
    baseline_pass = (element_baseline or {}).get("pass")
    if total_lib >= 1 and baseline_pass is True:
        dims["reusable_elements"] = _dim(
            STATUS_OK,
            "lib_topic/lib_hook/lib_copywriting/lib_quote/lib_method/lib_combo",
            f"积木元素 {total_lib} 条，元素基线通过",
            total_lib,
            by_table=lib_counts,
        )
    elif total_lib >= 1:
        reason = (element_baseline or {}).get("reason", "基线未达优")
        dims["reusable_elements"] = _dim(
            STATUS_WEAK,
            "lib_* 表",
            f"积木元素 {total_lib} 条，但{reason}",
            total_lib,
            by_table=lib_counts,
        )
    else:
        dims["reusable_elements"] = _dim(
            STATUS_MISSING, "lib_* 表", "无任何可复用积木元素", 0
        )

    status_count = {STATUS_OK: 0, STATUS_WEAK: 0, STATUS_MISSING: 0}
    for d in dims.values():
        status_count[d["status"]] = status_count.get(d["status"], 0) + 1
    return {
        "dimensions": dims,
        "labels": DIMENSION_LABELS,
        "status_count": status_count,
        "all_ok": status_count[STATUS_MISSING] == 0,
    }
