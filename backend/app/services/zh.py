"""简繁统一工具：把正文统一为简体中文。

背景：ASR（whisper）在中文转写时，受训练语料影响会输出繁体字
（如「我們」「實作」「產品」），下游拆解/创作台会被"传染"，导致
展示层出现繁体中文。项目口径是**全站简体**，因此在两个源头各做一次归一：

1. 源头一：ASR 转写文本（transcribe.py / pipeline.py）
2. 源头二：模型输出（analysis.py / creations.py，落库前统一处理）

实现优先用 zhconv（MediaWiki 转换表，质量最好）；未安装时退化为内置常用字表，
保证不因缺依赖而报错。
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

try:  # zhconv 为可选依赖
    import zhconv as _zhconv  # type: ignore
except Exception:  # noqa: BLE001
    _zhconv = None

# 兜底映射：仅覆盖高频繁体字（zhconv 缺失时仍能兜住绝大多数正文）
_FALLBACK_TABLE = str.maketrans(
    {
        "們": "们", "這": "这", "說": "说", "對": "对", "實": "实", "現": "现",
        "樣": "样", "聽": "听", "點": "点", "東": "东", "與": "与", "為": "为",
        "體": "体", "聲": "声", "號": "号", "後": "后", "給": "给", "問": "问",
        "題": "题", "國": "国", "際": "际", "萬": "万", "歲": "岁", "學": "学",
        "裡": "里", "髮": "发", "麼": "么", "廣": "广", "場": "场", "樂": "乐",
        "愛": "爱", "歡": "欢", "藍": "蓝", "綠": "绿", "紅": "红", "車": "车",
        "馬": "马", "鳥": "鸟", "魚": "鱼", "頭": "头", "臉": "脸", "覺": "觉",
        "認": "认", "識": "识", "講": "讲", "語": "语", "讀": "读", "寫": "写",
        "畫": "画", "節": "节", "錄": "录", "攝": "摄", "鏡": "镜", "時": "时",
        "間": "间", "開": "开", "關": "关", "門": "门", "飛": "飞", "機": "机",
        "產": "产", "業": "业", "務": "务", "價": "价", "場": "场",
        "值": "值", "錢": "钱", "買": "买", "賣": "卖", "費": "费", "單": "单",
        "簡": "简", "準": "准", "備": "备", "練": "练", "習": "习",
        "總": "总", "結": "结", "數": "数", "據": "据", "鏈": "链",
        "鍵": "键", "詞": "词", "註": "注", "釋": "释", "義": "义", "觀": "观",
        "眾": "众", "風": "风", "險": "险", "營": "营", "銷": "销", "賺": "赚",
        "標": "标", "題": "题", "熱": "热", "訊": "讯", "網": "网",
    }
)

# 判定"含繁体"的高频繁体独有字（简体写法不同且不通用）
_TRAD_HINT = set(
    "們這說對實現樣聽點東與為體聲號後給問題國際萬歲學裡髮麼廣場樂愛歡藍綠紅車馬鳥魚"
    "頭臉覺認識講語讀寫畫節錄攝鏡時間開關門飛機產業務價錢買賣費單簡準備練習總結數"
    "據鏈鍵詞註釋義觀眾風險營銷賺標題熱訊網"
)


def to_simplified(text: str) -> str:
    """把任意文本转成简体中文（失败时原样返回，绝不抛异常）。"""
    if not text:
        return text
    if _zhconv is not None:
        try:
            return _zhconv.convert(text, "zh-cn")
        except Exception:  # noqa: BLE001
            logger.warning("zhconv 转换失败，回退内置字表", exc_info=True)
    return text.translate(_FALLBACK_TABLE)


def has_traditional(text: str) -> bool:
    """粗略判断文本是否含繁体字（用于日志与质量自检）。"""
    if not text:
        return False
    return any(ch in _TRAD_HINT for ch in text)


def simplify_obj(obj: Any) -> Any:
    """递归把 JSON 结构里的所有字符串转简体（dict/list/str/其他原样）。"""
    if isinstance(obj, str):
        return to_simplified(obj)
    if isinstance(obj, dict):
        return {simplify_obj(k) if isinstance(k, str) else k: simplify_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [simplify_obj(v) for v in obj]
    return obj
