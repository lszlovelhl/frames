"""
三层分库拆解链路提示词管理。

每个层级的提示词独立存放，便于局部调整和 git 回滚。
修改某个层级的提示词时，只需要改对应文件，不影响其他层级。
"""

from .contract import THREE_LAYER_CONTRACT, LAYER_CODES, LAYER_LABELS, LAYER_ARRAY_KEYS
from .tl1 import _TL1
from .tl2 import _TL2
from .tl3 import _TL3
from .tl4 import _TL4
from .tl5 import _TL5
from .tl6 import _TL6
from .tl45 import _TL45

# 提示词模板注册表
TEMPLATES = {
    "tl1_topline": {
        "name": "三层分库·L1 本片定调",
        "layer": 1,
        "content": _TL1,
    },
    "tl2_curve": {
        "name": "三层分库·L2 连续情绪曲线",
        "layer": 2,
        "content": _TL2,
    },
    "tl3_segment": {
        "name": "三层分库·L3 段落切分",
        "layer": 3,
        "content": _TL3,
    },
    "tl4_sentence": {
        "name": "三层分库·L4 句子级还原",
        "layer": 4,
        "content": _TL4,
    },
    "tl5_lib": {
        "name": "三层分库·L5 积木提炼（六类分库）",
        "layer": 5,
        "content": _TL5,
    },
    "tl45_script": {
        "name": "三层分库·L4.5 完整脚本还原",
        "layer": 45,
        "content": _TL45,
    },
    "tl6_combo": {
        "name": "三层分库·L6 组合模板还原",
        "layer": 6,
        "content": _TL6,
    },
}

__all__ = [
    "THREE_LAYER_CONTRACT",
    "LAYER_CODES",
    "LAYER_LABELS",
    "LAYER_ARRAY_KEYS",
    "TEMPLATES",
    "_TL1",
    "_TL2",
    "_TL3",
    "_TL4",
    "_TL5",
    "_TL6",
    "_TL45",
]
