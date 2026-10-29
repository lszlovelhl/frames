"""离线回归合成样本（纯 dict，不 import 任何网络 / DB / 模型调用）。

覆盖一条 60s 口播短视频的结构化产物快照：句子、段落、情绪曲线统计、
积木计数、画面素材，供节奏推导 / 完播驱动 / 质量门 / 七维映射纯函数断言。
"""
from __future__ import annotations

# ---------------------------------------------------------------- 合成句子
# 12 句，覆盖：钩子 / 铺垫 / 转折 / 干货 / 高潮 / CTA 收尾
SAMPLE_SENTENCES: list[dict] = [
    {"seq": 1, "start_ms": 0, "end_ms": 3200, "quote": "你有没有刷到过越看越上头的短视频",
     "sentence_function": "钩子", "emotion_intensity": 7,
     "is_hook": True, "is_turn": False, "is_peak": False},
    {"seq": 2, "start_ms": 3200, "end_ms": 7000, "quote": "今天这条三秒钟讲清楚底层逻辑",
     "sentence_function": "铺垫", "emotion_intensity": 4,
     "is_hook": False, "is_turn": False, "is_peak": False},
    {"seq": 3, "start_ms": 7000, "end_ms": 8600, "quote": "结果很多人第一步就走错了",
     "sentence_function": "转折", "emotion_intensity": 6,
     "is_hook": False, "is_turn": True, "is_peak": False},
    {"seq": 4, "start_ms": 8600, "end_ms": 13000, "quote": "第一钩子必须出现在前三秒",
     "sentence_function": "干货", "emotion_intensity": 5,
     "is_hook": False, "is_turn": False, "is_peak": False},
    {"seq": 5, "start_ms": 13000, "end_ms": 17200, "quote": "第二节奏要每隔八秒抛一个新信息",
     "sentence_function": "干货", "emotion_intensity": 5,
     "is_hook": False, "is_turn": False, "is_peak": False},
    {"seq": 6, "start_ms": 17200, "end_ms": 21500, "quote": "真正的爆点在第四十秒才出现",
     "sentence_function": "转折", "emotion_intensity": 8,
     "is_hook": False, "is_turn": True, "is_peak": False},
    {"seq": 7, "start_ms": 21500, "end_ms": 26000, "quote": "数据显示留存率在这里断崖式拉升",
     "sentence_function": "铺垫", "emotion_intensity": 6,
     "is_hook": False, "is_turn": False, "is_peak": False},
    {"seq": 8, "start_ms": 26000, "end_ms": 33000, "quote": "镜头怼脸配合情绪上扬就是高潮段",
     "sentence_function": "高潮", "emotion_intensity": 9,
     "is_hook": False, "is_turn": False, "is_peak": True},
    {"seq": 9, "start_ms": 33000, "end_ms": 38500, "quote": "把这套模板套到你的选题上试一次",
     "sentence_function": "干货", "emotion_intensity": 5,
     "is_hook": False, "is_turn": False, "is_peak": False},
    {"seq": 10, "start_ms": 38500, "end_ms": 44000, "quote": "很多人照做之后第一条就起量了",
     "sentence_function": "铺垫", "emotion_intensity": 6,
     "is_hook": False, "is_turn": False, "is_peak": False},
    {"seq": 11, "start_ms": 44000, "end_ms": 52000, "quote": "最后记住一句话开头决定划走率",
     "sentence_function": "高潮", "emotion_intensity": 8,
     "is_hook": False, "is_turn": False, "is_peak": True},
    {"seq": 12, "start_ms": 52000, "end_ms": 58000, "quote": "点赞收藏下期拆解更多爆款结构",
     "sentence_function": "CTA", "emotion_intensity": 5,
     "is_hook": False, "is_turn": False, "is_peak": False},
]

# ---------------------------------------------------------------- 合成段落
SAMPLE_SEGMENTS: list[dict] = [
    {"seq": 1, "seg_type": "钩子", "start_ms": 0, "end_ms": 8600,
     "start_sentence_seq": 1, "end_sentence_seq": 3,
     "hook_point": True, "payoff_point": False, "title": "开场三连问", "summary": "前三秒抛出痛点悬念"},
    {"seq": 2, "seg_type": "干货", "start_ms": 8600, "end_ms": 21500,
     "start_sentence_seq": 4, "end_sentence_seq": 5,
     "hook_point": False, "payoff_point": False, "title": "方法论两点", "summary": "钩子前置 + 信息密度"},
    {"seq": 3, "seg_type": "冲突", "start_ms": 21500, "end_ms": 33000,
     "start_sentence_seq": 6, "end_sentence_seq": 8,
     "hook_point": False, "payoff_point": True, "title": "高潮兑现", "summary": "情绪峰值配合画面冲击"},
    {"seq": 4, "seg_type": "CTA", "start_ms": 33000, "end_ms": 58000,
     "start_sentence_seq": 9, "end_sentence_seq": 12,
     "hook_point": False, "payoff_point": False, "title": "行动号召", "summary": "引导收藏与下期期待"},
]

# ---------------------------------------------------------------- 情绪曲线统计（stage2 输出口径）
SAMPLE_CURVE_OK: dict = {
    "shape": "递增-回落",
    "peak_count": 2,
    "valley_count": 1,
    "sample_count": 120,
    "interpolated_ratio": 0.08,
    "degenerate": False,
    "series_max": 9.0,
    "series_min": 3.0,
    "method": "hybrid",
    "peak_position_ratio": 0.55,
}

SAMPLE_CURVE_DEGENERATE: dict = {
    "shape": "平直",
    "peak_count": 0,
    "valley_count": 0,
    "sample_count": 120,
    "interpolated_ratio": 0.42,
    "degenerate": True,
    "series_max": 5.2,
    "series_min": 4.6,
    "method": "audio_energy",
}

# ---------------------------------------------------------------- 积木计数
SAMPLE_LIB_COUNTS_OK: dict = {
    "lib_hook": 2, "lib_method": 1, "lib_topic": 1,
    "lib_quote": 1, "lib_copywriting": 1, "lib_combo": 1,
}

SAMPLE_LIB_COUNTS_EMPTY: dict = {
    "lib_hook": 0, "lib_method": 0, "lib_topic": 0,
    "lib_quote": 0, "lib_copywriting": 0, "lib_combo": 0,
}

# ---------------------------------------------------------------- 画面素材
SAMPLE_SHOTS: list[dict] = [
    {"seq": 1, "start_ms": 0, "desc": "主播怼脸特写，背景为直播间"},
    {"seq": 2, "start_ms": 26000, "desc": "情绪上扬时切近景字幕放大"},
]
SAMPLE_FRAME_EVIDENCE_N = 3

DURATION_MS = 60_000


def good_gate_payload() -> dict:
    """一份应当通过质量门的完整输入（与 pipeline 组装口径一致）。"""
    return {
        "core_idea": "用前三秒钩子与高信息密度节奏，讲清短视频留存逻辑",
        "summary": (
            "本片以三连问开场制造悬念，随后给出钩子前置、八秒一信息的方法论，"
            "中段用数据与情绪画面推至高潮，结尾引导收藏，整体起承转合完整、建议可执行。"
        ),
        "hooks_count": 3,
        "curve": SAMPLE_CURVE_OK,
        "rhythm": {
            "usable": True,
            "segment_count": 4,
            "coverage_ratio": 1.0,
            "pace_label": "中",
            "overall_sentence_density": 0.2,
            "note": "合成样本",
        },
        "quote_verbatim_rate": 0.92,
        "segment_coverage": 1.0,
        "lib_counts": SAMPLE_LIB_COUNTS_OK,
        "element_baseline": {"pass": True, "score": 82, "reason": ""},
    }
