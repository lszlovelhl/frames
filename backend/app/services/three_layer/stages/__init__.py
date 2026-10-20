"""三层拆解流水线阶段子模块。

将原 3600 行巨石拆解流水线解耦为独立阶段：
- common: 通用常量、格式化工具、_run_layer 及异常清理
- stage1_anchor: L1 本片定调与 ScriptScript 创建
- stage2_rhythm: L2 节奏快照与 ScriptEmotionCurve
- stage3_structure: L3 结构拆解与 ScriptSegment
- stage4_refine: L4 逐句精修、对齐与 ScriptSentence / full_script
- stage5_elements: L5/L6 元素沉淀、深化与组合模板
"""
from __future__ import annotations

from .common import (
    Progress,
    _PipelineAbort,
    _abort_cleanup,
    _clamp_intensity,
    _fmt_transcript,
    _looks_lyric,
    purge_three_layer,
)
from .stage1_anchor import run_stage1_anchor
from .stage2_rhythm import run_stage2_rhythm
from .stage3_structure import run_stage3_structure
from .stage4_refine import run_stage4_refine
from .stage5_elements import run_stage5_elements

__all__ = [
    "Progress",
    "_PipelineAbort",
    "_abort_cleanup",
    "_clamp_intensity",
    "_fmt_transcript",
    "_looks_lyric",
    "purge_three_layer",
    "run_stage1_anchor",
    "run_stage2_rhythm",
    "run_stage3_structure",
    "run_stage4_refine",
    "run_stage5_elements",
]
