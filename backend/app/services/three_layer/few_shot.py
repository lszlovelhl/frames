"""few-shot 示范库（P0 质量提升·三件套之一）

设计文档 docs/01-product-design.md 要求「每品类 1-2 个编导认可的满分拆解做
few-shot 样本示范」。本模块是该设计的第一版落地：按品类维护可复用的示范条目，
在 L5 积木提炼（prompts/tl5.py）时按需注入提示词，指导模型输出格式与深度。

对齐约束：
- 提示词契约 C-1~C-8（app/services/three_layer/prompts/contract.py）
- 校验规则 R1~R15（app/services/three_layer/validate.py）
  示范条目虽不落库，但字段必须「在真实运行时能通过校验语义」：
  - R2 枚举：hook_type / position / topic_type / value_type / copy_type / category /
    abstraction_level 全部取自白名单（ENUM_FALLBACK / tl5 枚举说明）
  - R5 code：三段式小写 code（topic.* / hook.* / copy.* / method.*）
  - R12 发散：variants ≥ 2 且两两相似度 < 0.8，imagination ≥ 30 字
  - R13 手法：usage_steps ≥ 30 字且含具体动词；counter_example 非空
  - R15 深度：mechanism ≥ 60 字且点名具体心理/传播机制（反常识冲突/信息缺口/
    峰终定律/社交货币/损失厌恶等），imagination ≥ 50 字且落到具体迁移动作
    （禁止"迁移到其他领域"式场景置换套话）
  - C-4 反标签：机制/想象说明禁止功能标签词与观后感评价词
- 品类路由：经 video.category_guess → categories.normalize_category 归一后按
  CATEGORY_ALIASES 命中示范组；未命中回退到通用示范（"通用"）。

新增品类示范：在 FEW_SHOT_SAMPLES 中按品类名追加条目即可，无需改 pipeline。
"""

from __future__ import annotations

import json
from typing import Any

# 每品类示范条目（key 与 categories.CATEGORY_TAXONOMY 成员一致，另有 "通用" 兜底）
# 结构：{品类: {"methods": [...], "hooks": [...], "topics": [...], "copywriting": [...]}}
FEW_SHOT_SAMPLES: dict[str, dict[str, list[dict[str, Any]]]] = {
    "知识口播": {
        "methods": [
            {
                "code": "method.struct.conclusion_first",
                "name": "结论先行·反常识锚点",
                "category": "结构",
                "abstraction_level": "段落级",
                "mechanism": (
                    "把反常识结论放在开头制造认知冲突：观众带着'这不可能'的疑问进入正文，"
                    "信息缺口驱动其追看验证过程；口播知识类视频前 3 秒是跳出率最高的位置，"
                    "先抛结论可利用首因效应锁住注意力，再逐条拆解依据完成悬念-释放。"
                    "若结论与正文证据脱节或题材本身无争议点，这一结构会显得标题党，"
                    "适用于有明确反常识结论的科普/职场/财经类选题。"
                ),
                "usage_steps": (
                    "先写一句反常识结论 → 再按证据强度逐条拆解支撑依据 → 然后用括号句"
                    "把每步结论回扣到中心思想 → 收尾用一句具体行动建议代替口号"
                ),
                "counter_example": (
                    "无反转点的常规知识点硬套'结论先行'，观众预期被抬高后落空，反而比平铺更差"
                ),
                "variants": [
                    {"text": "求职面试开场：先抛'HR 根本不在乎你准备多久自我介绍'，再拆招聘逻辑"},
                    {"text": "育儿视频：先抛'越催越慢'，再拆习惯养成机制，落回日常场景"},
                ],
                "imagination": (
                    "放到财经职场号：把'反常识结论'改成'存钱越多的人越容易踩中消费陷阱'，"
                    "保留结论先行-拆解-回扣三步，预期引发评论区晒账单争论，比知识口播的"
                    "理性认同多一层身份认同驱动转发。"
                ),
                "source_line_no": 3,
                "quote": "很多人不知道，越努力反而越焦虑",
            },
            {
                "code": "method.rhythm.three_point_pacing",
                "name": "三段式递进·知识点爬坡",
                "category": "节奏",
                "abstraction_level": "段落级",
                "mechanism": (
                    "把讲解拆成'概念→机制→应用'三段，每段结尾给一个可验证的小结论，"
                    "利用即时反馈维持学习型观众的确定性需求；第二段抛出机制层的认知升级"
                    "制造情绪落差，第三段落到应用层完成峰终收尾。适合逻辑链清晰的讲解，"
                    "若选题偏情绪/猎奇型，节奏爬坡会被感知为冗长。"
                ),
                "usage_steps": (
                    "先定三个递进知识点 → 第一段给概念与举例 → 第二段讲背后机制并用前文"
                    "案例回指 → 第三段给应用场景 → 每段结尾用 1 句短话总结留记忆点"
                ),
                "counter_example": (
                    "知识点之间没有因果递进时硬分三段，观众会觉得三段各讲各的、没有主线"
                ),
                "variants": [
                    {"text": "考研规划视频：概念段讲目标拆解，机制段讲记忆曲线，应用段给作息表"},
                    {"text": "装修避坑视频：概念段讲甲醛，机制段讲释放条件，应用段讲入住流程"},
                ],
                "imagination": (
                    "放到美食探店号：把知识点爬坡改成'发现→验证→结论'三步试吃结构，"
                    "预期观众跟随验证过程产生代入感，比知识号多一层身临其境的情绪传染。"
                ),
                "source_line_no": 7,
                "quote": "今天只讲一个点：为什么你的计划总完不成",
            },
        ],
        "topics": [
            {
                "code": "topic.pain.plan_failure",
                "name": "计划总完不成的机制拆解",
                "topic_type": "痛点型",
                "audience": "职场新人、备考学生",
                "pain_point": "计划立了又废、自我怀疑累积",
                "angle": "从执行机制而非自律角度切入",
                "value_type": "实用",
                "keywords": ["计划", "拖延", "执行", "机制"],
                "mechanism": (
                    "痛点型选题直接点名目标人群高频失败场景，利用损失厌恶让观众觉得"
                    "'不看就亏'；把失败归因从'我不自律'转为'机制设计问题'，制造认知失调"
                    "后的归因重估，观众为消除失调感而追完整条拆解。若机制解释与观众"
                    "实际经验差距过大，会产生被说教感，需保持'陪跑'而非'上课'语气。"
                ),
                "variants": [
                    {"text": "情侣相处：把'计划完不成'换成'道歉总失效'，拆沟通机制"},
                    {"text": "减肥号：把'自律'换成'复胖循环'，拆食欲-情绪反馈机制"},
                ],
                "imagination": (
                    "放到游戏账号：把'计划执行'改成'连跪循环'，保留归因重估三步，"
                    "预期游戏玩家对'机制设计'的类比接受度更高，评论会主动补充自己的"
                    "连跪案例，社交货币效应比知识号更强。"
                ),
                "source_line_no": 1,
                "quote": "为什么你越努力越焦虑",
                "quality_hint": 8,
            },
        ],
        "hooks": [
            {
                "code": "hook.curiosity.reverse_claim",
                "name": "反常识断言开场",
                "hook_type": "金句",
                "position": "前3秒",
                "sentence_pattern": "其实【常识对象】根本不【常见认知】，因为【关键变量】",
                "variables": ["常识对象", "常见认知", "关键变量"],
                "expected_effect": "制造认知冲突，拉高前 3 秒留存",
                "mechanism": (
                    "断言与观众既有认知相悖，触发认知失调后产生修复冲动，观众必须看完"
                    "验证逻辑；放在前 3 秒利用首因效应，把'划走决策'推迟到冲突被解释。"
                    "若断言过于离谱且无后续证据，会被判定为标题党，需保证结论可验证。"
                ),
                "variants": [
                    {"text": "理财号：'其实存钱越多的人越容易亏'，再拆消费陷阱"},
                    {"text": "职场号：'其实简历越完美越容易被筛掉'，再拆 HR 筛选逻辑"},
                ],
                "imagination": (
                    "放到母婴号：把断言改成'其实婴儿哭的时候抱得越勤越好'，保留冲突-"
                    "验证两步，预期引发新旧育儿观评论区对峙，争议本身成为二次传播燃料。"
                ),
                "source_line_no": 1,
                "quote": "很多人不知道，越努力反而越焦虑",
            },
        ],
    },
    "剧情短剧": {
        "methods": [
            {
                "code": "method.narrative.misunderstanding_loop",
                "name": "误会循环·三明治反转",
                "category": "叙事",
                "abstraction_level": "全片级",
                "mechanism": (
                    "利用信息差让观众掌握角色不知道的事实，悬念驱动追更；三明治结构"
                    "（误会→升级→真相）中每次升级都强化期待落差，最后一层反转释放"
                    "情绪落差并回填伏笔。短剧单集 1-3 分钟，循环节奏需在 30s 内完成"
                    "一次升级，若误会缺乏观众视角的'先知道'设计，则变成无意义的吵。"
                ),
                "usage_steps": (
                    "先埋观众已知而角色不知的事实 → 让误会从细节积累升级 → 在冲突顶点"
                    "揭真相 → 用真相回填前文细节 → 结尾留新误会的钩子"
                ),
                "counter_example": (
                    "误会没有任何信息差支撑，角色行为缺乏动机，观众只觉得角色降智"
                ),
                "variants": [
                    {"text": "职场剧：把误会换成'抢功'，观众先看到真实功劳归属再围观争功"},
                    {"text": "家庭剧：把误会换成'隐瞒病情'，升级靠一次次的善意谎言"},
                ],
                "imagination": (
                    "放到搞笑短剧：把误会循环改成'认错人'三明治，反转落点在荒诞而非"
                    "情感冲击，预期弹幕密度更高但完播深度下降，适配 30s 竖屏段子。"
                ),
                "source_line_no": 5,
                "quote": "原来你一直都知道，为什么不早点说",
            },
            {
                "code": "method.audio.impact_sfx",
                "name": "冲突爆点·音效卡点",
                "category": "视听",
                "abstraction_level": "句法级",
                "mechanism": (
                    "在台词爆点前 1 拍静默，制造悬念-释放的张力；鼓点落点对准关键词"
                    "形成视听同步，观众注意力被强制锚定。短视频 3 秒内无信息增量即跳出，"
                    "卡点把'停顿'变成'即将爆发'的预告，属即时反馈型设计。滥用会让全片"
                    "变成音效堆砌，每集使用不超过 2 处。"
                ),
                "usage_steps": (
                    "先定位台词中的爆点关键词 → 在关键词前留 0.5s 静默 → 鼓点与关键词"
                    "同帧落点 → 后接反应镜头放大情绪"
                ),
                "counter_example": (
                    "无情绪起伏的日常台词也配卡点音效，观众会疲劳并察觉套路"
                ),
                "variants": [
                    {"text": "综艺切片：把卡点对准导师转身的瞬间"},
                    {"text": "探店视频：把卡点对准第一口入嘴的反应"},
                ],
                "imagination": (
                    "放到运动健身号：把爆点卡点改成动作发力点同步节拍，预期比知识号"
                    "更有'现场感'，配合慢放可强化视觉冲击但会牺牲信息密度，适配短时长"
                    "燃脂教程。"
                ),
                "source_line_no": 9,
                "quote": "还要说吗，还要放吗",
            },
        ],
        "hooks": [
            {
                "code": "hook.conflict.midscene_turn",
                "name": "中途对峙·关系突变",
                "hook_type": "悬念",
                "position": "片中",
                "sentence_pattern": "【A】突然对【B】说出【反转关键句】，全场安静",
                "variables": ["A", "B", "反转关键句"],
                "expected_effect": "制造对峙，把观众从日常叙事拉回主线冲突",
                "mechanism": (
                    "对峙句打破前文的平静状态，触发情绪落差，观众为确认'接下来会怎样'"
                    "而放弃划走；把关键信息以对质方式抛出，比旁白陈述多一层人际张力，"
                    "悬念-释放节奏更紧。若反转句与前文铺垫无因果勾连，会被视为强行冲突。"
                ),
                "variants": [
                    {"text": "职场剧：'这份方案是你抄的吧'，全场安静"},
                    {"text": "家庭剧：'妈，我其实没有考上'，切家人反应镜头"},
                ],
                "imagination": (
                    "放到情感口播号：把对峙句改成采访式'你当时为什么不解释'，保留悬念"
                    "-释放两步，预期故事号比剧情号多一层真实感信任，评论更愿意站队。"
                ),
                "source_line_no": 6,
                "quote": "你到现在还在装傻吗",
            },
        ],
    },
    "美食": {
        "copywriting": [
            {
                "code": "copy.contrast.sensory_anchor",
                "name": "反差感官·声音前置",
                "copy_type": "字幕",
                "sentence_pattern": "【动作】+【声音拟词】+【反差画面】，一口下去【夸张后果】",
                "rhetoric": "夸张",
                "example_text": "一口下去，脆得整个办公室都回头",
                "mechanism": (
                    "用声音拟词与反差画面把味觉转译成听觉/视觉符号，观众无法亲尝但能"
                    "通过感官迁移产生代入感；'办公室回头'制造社交货币，观众会想转发"
                    "给同事。若文案只堆形容词不落具体动作，就会变成'好吃到爆'式空话。"
                ),
                "variants": [
                    {"text": "探店字幕：'第一口，辣得手都在抖'配辣椒特写"},
                    {"text": "零食测评：'咔嚓一声，连袋口都酥出了边'配慢放碎裂"},
                ],
                "imagination": (
                    "放到家居号：把味觉转译改成触觉转译'一躺下去，蓬得手机都弹起来了'，"
                    "保留感官迁移结构，预期家居观众对'体感证据'的信任度高于美食观众的"
                    "馋意，决策型评论更集中。"
                ),
                "source_line_no": 4,
                "quote": "脆得整个办公室都回头",
            },
        ],
        "methods": [
            {
                "code": "method.structure.reveal_first",
                "name": "成品前置·反差开场",
                "category": "结构",
                "abstraction_level": "段落级",
                "mechanism": (
                    "先展示成品的极端状态（拉丝/爆汁/塌陷），利用意外感制造'这怎么做的'"
                    "好奇缺口；成品-过程的反差让观众停留在烹饪全程，最后回放成品完成"
                    "峰终闭环。美食类前 3 秒无成品冲击则等同普通做饭 vlog，差异化靠"
                    "极端状态与制作过程的反差。对家常菜无冲击点的品类，需先找到"
                    "该菜唯一的视觉爆点。"
                ),
                "usage_steps": (
                    "先拍成品极端状态 3 秒 → 剪过程时保留 1 个'几乎翻车'悬念 → 复现成品"
                    " → 结尾再给一口入嘴的满足反应"
                ),
                "counter_example": (
                    "成品本身平淡的菜硬套反差开场，观众看完觉得被骗，反感大于好奇"
                ),
                "variants": [
                    {"text": "烘焙号：先展示切开瞬间流心，再剪搅拌翻车过程"},
                    {"text": "路边摊号：先展示排队 200 米，再剪到手第一口"},
                ],
                "imagination": (
                    "放到萌宠号：把成品前置改成'睡姿冲击-起床反差'，保留极端状态开场-"
                    "过程-闭环三步，预期宠物观众因可爱+意外双驱动完播，比美食号多一层"
                    "云吸宠的情绪价值。"
                ),
                "source_line_no": 1,
                "quote": "这锅汤，端上来还在咕嘟",
            },
        ],
        "hooks": [
            {
                "code": "hook.benefit.extreme_promise",
                "name": "极端结果·利益承诺",
                "hook_type": "利益承诺",
                "position": "前3秒",
                "sentence_pattern": "这样做【动作】，【结果】直接【夸张后果】",
                "variables": ["动作", "结果", "夸张后果"],
                "expected_effect": "用极端结果承诺拉满期待，防止前 3 秒划走",
                "mechanism": (
                    "利益承诺把'看完有什么收获'前置，观众按损失厌恶评估'不看就亏'；"
                    "极端化结果放大期待落差，驱动观众看完整条验证。若结果无法兑现或"
                    "明显夸张过度，会触发'被骗感'，反而损伤账号信任，承诺需有可验证"
                    "的过程证据。"
                ),
                "variants": [
                    {"text": "健身号：'这样做 7 天，腰围直接小一号'"},
                    {"text": "护肤号：'这样涂，出油直接少一半'"},
                ],
                "imagination": (
                    "放到汽车号：把结果承诺改成'这样开，油耗直接降三分之一'，保留利益"
                    "前置结构，预期汽车观众的验证行为更理性（看仪表盘），比美食号更依赖"
                    "数据证据支撑。"
                ),
                "source_line_no": 2,
                "quote": "照这个方法腌，鸡肉嫩得不像话",
            },
        ],
    },
    "通用": {
        "methods": [
            {
                "code": "method.struct.hook_loop",
                "name": "钩子-展开-回扣三步闭环",
                "category": "结构",
                "abstraction_level": "全片级",
                "mechanism": (
                    "开头钩子制造好奇缺口，中段展开用具体证据逐步满足，结尾回扣钩子"
                    "完成悬念-释放闭环；回扣让观众产生'完整感'，峰终定律下结尾印象"
                    "决定转发意愿。该结构对任何题材都成立，但钩子强度必须匹配题材"
                    "本身的信息量，信息密度低的题材强行拉钩子会空转。"
                ),
                "usage_steps": (
                    "先写钩子句（含具体悬念）→ 展开段按证据强弱排序 → 结尾回指钩子"
                    "原词并给一个超出预期的收尾结论"
                ),
                "counter_example": (
                    "钩子与正文主体脱节，观众看完发现正文根本没回答开场问题"
                ),
                "variants": [
                    {"text": "科技号：钩子'这个功能藏了三年'，展开拆更新日志"},
                    {"text": "亲子号：钩子'这句话千万别对孩子说'，展开拆语言伤害研究"},
                ],
                "imagination": (
                    "放到任何品类均可：保持'具体悬念→证据→回扣'，只需替换题材的事实"
                    "密度与钩子强度，预期通用结构在不同品类间迁移无损耗，属于跨题材"
                    "不变的骨架。"
                ),
                "source_line_no": 1,
                "quote": "今天这条，建议你看到最后",
            },
        ],
        "hooks": [
            {
                "code": "hook.curiosity.open_loop",
                "name": "开放式悬念·未完成句",
                "hook_type": "悬念",
                "position": "前3秒",
                "sentence_pattern": "直到【时机】，我才发现【反转事实】",
                "variables": ["时机", "反转事实"],
                "expected_effect": "留未闭合信息缺口，拉高留存",
                "mechanism": (
                    "未完成句暴露信息缺口但不给出口，观众按好奇心缺口模型自动补全"
                    "期待，必须看到闭合；时间词'直到'暗示转折即将发生，制造紧迫感。"
                    "若缺口长时间不闭合或闭合与缺口无关，观众会产生被戏弄感。"
                ),
                "variants": [
                    {"text": "职场号：'直到被裁员，我才发现简历的问题'"},
                    {"text": "旅游号：'直到飞机落地，我才发现攻略全是错的'"},
                ],
                "imagination": (
                    "放到学习号：把'直到'改成'刷了 100 套题才懂'，预期备考人群因身份"
                    "认同而代入，评论会晒自己的'直到'时刻，社交货币驱动二次传播。"
                ),
                "source_line_no": 1,
                "quote": "直到第 100 次失败，我才明白问题出在哪",
            },
        ],
    },
}

# 每个示范组默认渲染顺序（对齐 tl5 输出数组顺序）
_SAMPLE_ORDER: tuple[str, ...] = ("topics", "hooks", "copywriting", "quotes", "methods")


def load_few_shots(category: str | None = None, *, layer: int | None = None) -> list[dict[str, Any]]:
    """按品类取示范条目（支持 layer 过滤，layer=None 取全部）。

    品类未命中（None / 空串 / 不在表内）时回退"通用"示范组。
    """
    group = FEW_SHOT_SAMPLES.get(category) or FEW_SHOT_SAMPLES["通用"]
    items: list[dict[str, Any]] = []
    for key in _SAMPLE_ORDER:
        if key not in group:
            continue
        if layer is not None and layer != 5:
            continue  # 目前示范条目均为 L5 积木，非 L5 层不注入
        items.extend(group[key])
    return items


def render_few_shots(category: str | None = None, *, layer: int | None = None) -> str:
    """渲染成提示词注入文本（纯文本 + JSON 示范，供 L5 提示词追加）。"""
    items = load_few_shots(category, layer=layer)
    if not items:
        return ""
    group_name = category or "通用"
    lines = [
        f"【few-shot 示范（{group_name}品类满分拆解样本，供格式与深度参考，不得照抄 quote）】"
    ]
    for item in items:
        lines.append(json.dumps(item, ensure_ascii=False, indent=1))
    lines.append(
        "以上为示范样本：枚举字段必须取自白名单；code 为三段式小写；"
        "mechanism 必须点名具体机制且 ≥60 字；imagination 必须落到具体迁移动作且 ≥50 字；"
        "variants 至少 2 条、每条 ≥15 字且互不相似；quote 必须逐字来自原片。"
    )
    return "\n\n".join(lines)
