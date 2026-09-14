"""内容赛道（category）常量

拆解库 / 元素库除按平台分组外，还可按内容赛道（category）分组。
赛道标签尽量贴近编导实际心智：跨平台通用、颗粒度适中。

注意：新增赛道需同步考虑 scripts/backfill_categories.py 与前端 TAXONOMY 顺序；
本列表为服务端权威排序来源（video.category_guess 只存单个中文标签）。
"""

# 赛道全集（顺序即推荐展示顺序）
CATEGORY_TAXONOMY: list[str] = [
    "知识口播",
    "剧情短剧",
    "美食",
    "搞笑",
    "美妆",
    "萌宠",
    "游戏",
    "音乐舞蹈",
    "运动健身",
    "情感",
    "生活记录",
    "科技数码",
    "财经职场",
    "汽车出行",
    "文旅非遗",
    "综艺娱乐",
    "亲子育儿",
    "影视解说",
    "时尚穿搭",
    "好物测评",
]

# 容错别名：模型/标题出现的口语叫法 → 归一标签
CATEGORY_ALIASES: dict[str, str] = {
    "知识": "知识口播",
    "知识分享": "知识口播",
    "口播": "知识口播",
    "科普": "知识口播",
    "教学": "知识口播",
    "剧情": "剧情短剧",
    "短剧": "剧情短剧",
    "段子": "搞笑",
    "搞笑段子": "搞笑",
    "美食探店": "美食",
    "吃播": "美食",
    "美妆护肤": "美妆",
    "穿搭": "时尚穿搭",
    "时尚": "时尚穿搭",
    "宠物": "萌宠",
    "萌宠日常": "萌宠",
    "电竞": "游戏",
    "手游": "游戏",
    "数码": "科技数码",
    "科技": "科技数码",
    "3c": "科技数码",
    "财经": "财经职场",
    "职场": "财经职场",
    "健身": "运动健身",
    "运动": "运动健身",
    "汽车": "汽车出行",
    "旅行": "文旅非遗",
    "旅游": "文旅非遗",
    "非遗": "文旅非遗",
    "文化": "文旅非遗",
    "亲子": "亲子育儿",
    "育儿": "亲子育儿",
    "电影解说": "影视解说",
    "剧集解说": "影视解说",
    "解说": "影视解说",
    "测评": "好物测评",
    "好物": "好物测评",
}


def normalize_category(raw: str | None) -> str:
    """将模型/外部返回的任意标签归一为赛道全集成员；无法归一则返回 ""。"""
    if not raw:
        return ""
    s = str(raw).strip().strip("#＃").strip()
    if not s:
        return ""
    # 精确命中
    if s in CATEGORY_TAXONOMY:
        return s
    # 别名/子串命中（优先完整别名，再子串包含）
    if s in CATEGORY_ALIASES:
        return CATEGORY_ALIASES[s]
    for alias, cat in CATEGORY_ALIASES.items():
        if alias in s:
            return cat
    for cat in CATEGORY_TAXONOMY:
        if cat in s:
            return cat
    return ""


def rank_of(category: str | None) -> int:
    """赛道排序权重；未分类排最后。"""
    if not category:
        return 999
    try:
        return CATEGORY_TAXONOMY.index(category)
    except ValueError:
        return 998


# 产品行业全集（产品库一级分类，独立于内容赛道；顺序即展示顺序）
INDUSTRY_TAXONOMY: list[str] = [
    "汽车",
    "数码3C",
    "美妆个护",
    "服饰穿搭",
    "食品饮料",
    "家用电器",
    "家居家装",
    "母婴亲子",
    "运动户外",
    "宠物生活",
    "游戏文娱",
    "健康医疗",
    "金融保险",
    "教育学习",
    "旅游",
]

INDUSTRY_ALIASES: dict[str, str] = {
    "汽车": "汽车",
    "汽车出行": "汽车",
    "车": "汽车",
    "新能源车": "汽车",
    "3c": "数码3C",
    "数码": "数码3C",
    "手机": "数码3C",
    "电脑": "数码3C",
    "美妆": "美妆个护",
    "护肤": "美妆个护",
    "化妆品": "美妆个护",
    "服装": "服饰穿搭",
    "服饰": "服饰穿搭",
    "穿搭": "服饰穿搭",
    "食品": "食品饮料",
    "家电": "家用电器",
    "家居": "家居家装",
    "母婴": "母婴亲子",
    "运动": "运动户外",
    "户外": "运动户外",
    "健身": "运动户外",
    "宠物": "宠物生活",
    "游戏": "游戏文娱",
    "医疗": "健康医疗",
    "保险": "金融保险",
    "金融": "金融保险",
    "教育": "教育学习",
    "旅游": "旅游",
    "旅游出行": "旅游",
}


def normalize_industry(raw: str | None) -> str:
    """产品行业归一；无法归一则返回 ''。"""
    if not raw:
        return ""
    s = str(raw).strip().strip("#＃").strip()
    if not s:
        return ""
    if s in INDUSTRY_TAXONOMY:
        return s
    if s in INDUSTRY_ALIASES:
        return INDUSTRY_ALIASES[s]
    for alias, ind in INDUSTRY_ALIASES.items():
        if alias in s:
            return ind
    for ind in INDUSTRY_TAXONOMY:
        if ind in s:
            return ind
    return ""

