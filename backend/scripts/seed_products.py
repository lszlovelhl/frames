"""产品库种子：首批主流产品（参数与卖点取公开可核实的保守信息）
用法：cd backend && .venv/bin/python scripts/seed_products.py
幂等：brand + name 已存在且未变化则跳过。
注意：产品库数据讲究准确性，扩充请以官方/权威渠道为准；本条 seed 宁少而准。
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app import models as M  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402

PRODUCTS: list[dict] = [
    # ---------------- 汽车 ----------------
    {
        "industry": "汽车",
        "category_tags": ["汽车出行", "好物测评", "科技数码"],
        "brand": "小米",
        "name": "SU7",
        "series": "C 级纯电轿跑",
        "headline": "年轻人的第一台轿跑：2.78s 零百 + 人车家全生态互联",
        "price_range": "21.59 万起",
        "specs": [
            {"k": "级别", "v": "中大型纯电轿车"},
            {"k": "零百加速", "v": "Max 版 2.78s"},
            {"k": "CLTC 续航", "v": "700km 起 / Max 约 830km"},
            {"k": "风阻系数", "v": "Cd 0.195"},
            {"k": "快充平台", "v": "800V 高压平台（Max 版）"},
        ],
        "selling_points": [
            {"title": "人车家全生态", "detail": "澎湃 OS 打通手机-车机-家居，账号与 App 无缝流转"},
            {"title": "轿跑造型 + 赛道基因", "detail": "低趴姿态、大尾翼可选，MAX 性能直指同级跑车"},
            {"title": "智能座舱", "detail": "16.1 英寸中控 + 翻转仪表 + 后排拓展屏，支持 CarPlay"},
        ],
    },
    {
        "industry": "汽车",
        "category_tags": ["汽车出行", "科技数码"],
        "brand": "问界",
        "name": "M9",
        "series": "旗舰级全景智慧旗舰 SUV",
        "headline": "华为全家桶上车：ADS 高阶智驾 + 途灵底盘 + 零重力座椅",
        "price_range": "46.98 万起",
        "specs": [
            {"k": "动力", "v": "增程 / 纯电双版本"},
            {"k": "智驾", "v": "华为 ADS 3.0 高阶智驾（无图城区）"},
            {"k": "座舱", "v": "HarmonyOS 座舱 + HUAWEI SOUND"},
            {"k": "座椅", "v": "女王/零重力座椅可选"},
        ],
        "selling_points": [
            {"title": "智驾天花板", "detail": "华为 ADS 无图智驾，城市/高速覆盖能力强"},
            {"title": "底盘黑科技", "detail": "途灵底盘：CDC + 空气悬架联动，舒适与操控兼得"},
            {"title": "鸿蒙生态协同", "detail": "手机无缝流转、车家互联，华为用户无感连接"},
        ],
    },
    {
        "industry": "汽车",
        "category_tags": ["汽车出行", "生活记录"],
        "brand": "理想",
        "name": "L6",
        "series": "家庭豪华五座 SUV",
        "headline": "奶爸神车平替版：增程无焦虑 + 大空间 + 冰箱彩电大沙发",
        "price_range": "24.98 万起",
        "specs": [
            {"k": "动力", "v": "增程式（1.5T 增程器 + 双电机）"},
            {"k": "综合续航", "v": "CLTC 1390km（纯电 212km）"},
            {"k": "座舱", "v": "双 15.7 英寸屏 + 后排娱乐屏可选"},
            {"k": "悬架", "v": "前双叉臂 + 后五连杆"},
        ],
        "selling_points": [
            {"title": "家庭场景为王", "detail": "后排大空间 + 大床模式，露营/带娃场景直接命中"},
            {"title": "增程无里程焦虑", "detail": "可油可电，长途补能毫无压力"},
            {"title": "智能化平权", "detail": "高阶辅助驾驶全系标配能力下放"},
        ],
    },
    {
        "industry": "汽车",
        "category_tags": ["汽车出行", "财经职场"],
        "brand": "比亚迪",
        "name": "秦L DM-i",
        "series": "中级轿车 · 第五代 DM",
        "headline": "9.98 万起的续航之王：亏电油耗 2.9L、满油满电 2100km",
        "price_range": "9.98 万起",
        "specs": [
            {"k": "动力", "v": "第五代 DM-i 插混"},
            {"k": "亏电油耗", "v": "2.9L/100km（NEDC）"},
            {"k": "综合续航", "v": "CLTC 满油满电最长 2100km"},
            {"k": "车身", "v": "中级轿车（轴距 2790mm）"},
        ],
        "selling_points": [
            {"title": "成本杀手", "detail": "把中级轿车价格打到 10 万内，油耗低到反常识"},
            {"title": "超长续航", "detail": "一箱油跑 2100km，通勤/长途都省心"},
            {"title": "国民级认可", "detail": "上市即爆款，销量长期霸榜家用轿车"},
        ],
    },
    # ---------------- 数码3C ----------------
    {
        "industry": "数码3C",
        "category_tags": ["科技数码", "好物测评"],
        "brand": "Apple",
        "name": "iPhone 16 Pro",
        "series": "6.3 英寸专业级旗舰",
        "headline": "视频创作者的移动主力机：4K120 杜比视界 + 相机控制键",
        "price_range": "约 7999 元起",
        "specs": [
            {"k": "芯片", "v": "A18 Pro"},
            {"k": "屏幕", "v": "6.3 英寸超视网膜 XDR（ProMotion 120Hz）"},
            {"k": "影像", "v": "4800 万主摄 + 5 倍长焦 + 相机控制键"},
            {"k": "视频", "v": "4K 120fps 杜比视界"},
            {"k": "机身材质", "v": "钛金属中框"},
        ],
        "selling_points": [
            {"title": "视频能力拉满", "detail": "4K120 电影级录制 + 空间音频，移动端创作利器"},
            {"title": "相机控制键", "detail": "物理按键快速调参，拍片手感接近专业机"},
            {"title": "生态无缝", "detail": "与 Mac/iPad 接力流畅，剪辑工作流顺滑"},
        ],
    },
    {
        "industry": "数码3C",
        "category_tags": ["科技数码", "好物测评", "知识口播"],
        "brand": "华为",
        "name": "Mate 70 Pro",
        "series": "鸿蒙旗舰",
        "headline": "红枫原色影像 + 卫星通信，国产旗舰信号与影像双标杆",
        "price_range": "约 6499 元起",
        "specs": [
            {"k": "芯片", "v": "麒麟 9020"},
            {"k": "屏幕", "v": "6.9 英寸 OLED 等深四曲屏"},
            {"k": "影像", "v": "红枫原色影像 + 可变光圈主摄"},
            {"k": "通信", "v": "北斗卫星消息（无地面信号可收发）"},
            {"k": "系统", "v": "HarmonyOS（支持原生鸿蒙应用）"},
        ],
        "selling_points": [
            {"title": "信号与卫星通信", "detail": "弱网/无网场景下仍可联系，商务安全感强"},
            {"title": "红枫原色影像", "detail": "人像肤色自然真实，色彩调校口碑极佳"},
            {"title": "鸿蒙生态", "detail": "多设备协同、无缝流转体验成熟"},
        ],
    },
    {
        "industry": "数码3C",
        "category_tags": ["科技数码", "好物测评"],
        "brand": "小米",
        "name": "15",
        "series": "小屏旗舰 · 骁龙 8 至尊版",
        "headline": "一手可握的小屏旗舰：徕卡 Summilux 光学 + 5400mAh",
        "price_range": "约 4499 元起",
        "specs": [
            {"k": "芯片", "v": "骁龙 8 至尊版"},
            {"k": "屏幕", "v": "6.36 英寸 1.5K OLED 小直屏"},
            {"k": "影像", "v": "徕卡 Summilux 光学 + 光影猎人传感器"},
            {"k": "电池", "v": "5400mAh + 90W 有线 + 50W 无线"},
            {"k": "防护", "v": "IP68 防尘防水"},
        ],
        "selling_points": [
            {"title": "小屏手感大杯配置", "detail": "主流尺寸里少有的精致直屏旗舰"},
            {"title": "徕卡影调", "detail": "色彩风格讨喜，随手拍出氛围感"},
            {"title": "续航越级", "detail": "5400mAh 塞进小机身，续航口碑好"},
        ],
    },
    {
        "industry": "数码3C",
        "category_tags": ["科技数码", "知识口播", "好物测评"],
        "brand": "Apple",
        "name": "iPad Pro 13（M4）",
        "series": "生产力平板",
        "headline": "比纸还薄的专业创作平板：M4 芯片 + 双层串联 OLED",
        "price_range": "约 11499 元起",
        "specs": [
            {"k": "芯片", "v": "Apple M4"},
            {"k": "屏幕", "v": "13 英寸 Ultra Retina XDR（双层串联 OLED）"},
            {"k": "厚度", "v": "5.1mm（史上最薄 Apple 产品）"},
            {"k": "配件", "v": "支持 Apple Pencil Pro + 新妙控键盘"},
        ],
        "selling_points": [
            {"title": "M4 性能怪兽", "detail": "视频剪辑/绘画/AI 大模型本地推理都轻松"},
            {"title": "顶级屏幕", "detail": "双层串联 OLED 亮度与黑场表现顶级"},
            {"title": "轻薄便携", "detail": "5.1mm 机身，随时架起来就是移动创作台"},
        ],
    },
    {
        "industry": "数码3C",
        "category_tags": ["科技数码", "好物测评", "音乐舞蹈"],
        "brand": "Apple",
        "name": "AirPods Pro 2",
        "series": "USB-C 版主动降噪耳机",
        "headline": "降噪第一梯队的通勤神器：自适应音频 + 空间音频",
        "price_range": "约 1899 元",
        "specs": [
            {"k": "芯片", "v": "H2"},
            {"k": "降噪", "v": "主动降噪 2 倍提升 + 自适应通透"},
            {"k": "音频", "v": "个性化空间音频"},
            {"k": "续航", "v": "单次 6 小时（含充电盒 30 小时）"},
        ],
        "selling_points": [
            {"title": "降噪口碑王", "detail": "通勤/专注场景的默认答案"},
            {"title": "自适应音频", "detail": "环境噪音自动调节降噪深度，佩戴无感"},
            {"title": "生态体验", "detail": "iPhone/Mac/Apple Watch 无感切换"},
        ],
    },
    # ---------------- 美妆个护 ----------------
    {
        "industry": "美妆个护",
        "category_tags": ["美妆", "好物测评"],
        "brand": "珀莱雅",
        "name": "红宝石精华 3.0",
        "series": "抗老精华 · 国货标杆",
        "headline": "国货抗老扛把子：胜肽 + A 醇双通路，性价比碾压大牌",
        "price_range": "约 259-349 元",
        "specs": [
            {"k": "主打成分", "v": "六胜肽 + 视黄醇（A 醇）+ 神经酰胺"},
            {"k": "功效", "v": "淡纹紧致、细腻肤质"},
            {"k": "质地", "v": "轻润乳液，A 醇温和包裹"},
        ],
        "selling_points": [
            {"title": "双抗老通路", "detail": "胜肽放松表情纹 + A 醇促进胶原，覆盖动态/静态纹"},
            {"title": "温和包裹 A 醇", "detail": "降低刺激，新手也敢入门"},
            {"title": "大牌平替心智", "detail": "三百元档打千元级抗老体验，测评常客"},
        ],
    },
    {
        "industry": "美妆个护",
        "category_tags": ["美妆", "好物测评", "知识口播"],
        "brand": "薇诺娜",
        "name": "舒敏保湿特护霜",
        "series": "敏感肌修护",
        "headline": "敏感肌急救王牌：马齿苋 + 青刺果，泛红干痒当天安抚",
        "price_range": "约 68-88 元 / 50g",
        "specs": [
            {"k": "主打成分", "v": "云南马齿苋提取 + 青刺果油"},
            {"k": "功效", "v": "舒缓泛红、修护屏障、保湿"},
            {"k": "质地", "v": "轻润霜，无香精无酒精"},
        ],
        "selling_points": [
            {"title": "敏感肌第一联想", "detail": "皮肤科背书，换季/医美后修护首选"},
            {"title": "即时舒缓", "detail": "泛红干痒急救场景口碑极强"},
            {"title": "精简配方", "detail": "无酒精香精，屏障脆弱期友好"},
        ],
    },
    {
        "industry": "美妆个护",
        "category_tags": ["美妆", "好物测评"],
        "brand": "雅诗兰黛",
        "name": "小棕瓶精华（第七代）",
        "series": "修护精华 · 经典款",
        "headline": "熬夜脸的经典救星：二裂酵母 + 律波肽，维稳修护一本万利",
        "price_range": "约 850-1200 元 / 50ml",
        "specs": [
            {"k": "主打成分", "v": "二裂酵母发酵产物溶胞物 + 律波肽"},
            {"k": "功效", "v": "修护维稳、抗初老、改善暗沉"},
            {"k": "质地", "v": "淡黄色蛋清质地，好吸收"},
        ],
        "selling_points": [
            {"title": "经典长青款", "detail": "畅销几十年，修护精华品类的锚点产品"},
            {"title": "熬夜修护", "detail": "熬夜后肤色稳定，社交平台常年回购"},
            {"title": "百搭不挑皮", "detail": "干油敏都可作打底精华"},
        ],
    },
    {
        "industry": "美妆个护",
        "category_tags": ["美妆", "好物测评"],
        "brand": "兰蔻",
        "name": "小黑瓶精华（第二代）",
        "series": "肌底液 · 经典款",
        "headline": "肌底液鼻祖：Bio-7 生物专研配方，打开皮肤吸收通道",
        "price_range": "约 760-1080 元 / 50ml",
        "specs": [
            {"k": "主打成分", "v": "Bio-7 生物专研配方（二裂酵母 + 益生元等）"},
            {"k": "功效", "v": "强韧肌底、细腻毛孔、促后续吸收"},
            {"k": "质地", "v": "蛋清轻薄，秒吸收"},
        ],
        "selling_points": [
            {"title": "肌底液品类定义者", "detail": "先用小黑瓶再叠精华的搭配深入人心"},
            {"title": "微生物护肤", "detail": "主打皮肤微生态平衡，概念超前"},
            {"title": "肤感极佳", "detail": "轻薄不粘，油皮友好"},
        ],
    },
    {
        "industry": "美妆个护",
        "category_tags": ["美妆", "好物测评"],
        "brand": "SK-II",
        "name": "神仙水",
        "series": "精华水 · 传奇单品",
        "headline": "一瓶水养出透亮肌：90% PITERA 发酵精华，调节水油平衡",
        "price_range": "约 990-1600 元 / 230ml",
        "specs": [
            {"k": "核心成分", "v": "90% 以上 PITERA"},
            {"k": "功效", "v": "调节水油、细腻毛孔、提亮匀净"},
            {"k": "质地", "v": "清爽水状，拍/湿敷均可"},
        ],
        "selling_points": [
            {"title": "传奇口水味", "detail": "越用越懂，油皮亲妈标签深入人心"},
            {"title": "水油平衡", "detail": "长期使用皮肤状态稳定透亮"},
            {"title": "贵妇入门款", "detail": "送礼与自用双高热度单品"},
        ],
    },
    # ---------------- 服饰穿搭 ----------------
    {
        "industry": "服饰穿搭",
        "category_tags": ["时尚穿搭", "运动健身", "好物测评"],
        "brand": "lululemon",
        "name": "Align 瑜伽裤",
        "series": "女士裸感紧身裤",
        "headline": "瑜伽裤界的顶流：Nulu 面料裸感亲肤，运动穿搭两相宜",
        "price_range": "约 680-880 元",
        "specs": [
            {"k": "面料", "v": "Nulu 亲肤面料（高弹塑形）"},
            {"k": "设计", "v": "高腰 + 平缝工艺，无勒痕"},
            {"k": "场景", "v": "瑜伽/普拉提/日常通勤"},
        ],
        "selling_points": [
            {"title": "裸感鼻祖", "detail": "Nulu 面料让“穿上像没穿”成为品类标配"},
            {"title": "社媒出镜王", "detail": "瑜伽博主/健身打卡必备，辨识度极高"},
            {"title": "耐穿不变形", "detail": "多色可叠穿，复购率极高的常青款"},
        ],
    },
    {
        "industry": "服饰穿搭",
        "category_tags": ["时尚穿搭", "运动健身", "好物测评"],
        "brand": "ARC'TERYX",
        "name": "Beta LT 冲锋衣",
        "series": "男士全能硬壳",
        "headline": "户外硬壳天花板：GORE-TEX 全天候防护，一件顶三季",
        "price_range": "约 4500-5500 元",
        "specs": [
            {"k": "面料", "v": "GORE-TEX 3L 防水透气"},
            {"k": "版型", "v": "运动剪裁，兼容中间层"},
            {"k": "重量", "v": "约 425g（M 码）"},
            {"k": "兜帽", "v": "兼容头盔的帽兜设计"},
        ],
        "selling_points": [
            {"title": "鸟家平替中的正主", "detail": "城市户外皆可，中产穿搭符号"},
            {"title": "防水透气平衡", "detail": "暴雨级防护 + 不闷汗"},
            {"title": "保值耐穿", "detail": "耐用十年，二手市场也坚挺"},
        ],
    },
    {
        "industry": "服饰穿搭",
        "category_tags": ["时尚穿搭", "好物测评"],
        "brand": "Nike",
        "name": "Dunk Low",
        "series": "经典滑板鞋",
        "headline": "联名配色发动机：经典板鞋造型，一双鞋承载球鞋文化",
        "price_range": "约 699-899 元（普通款）",
        "specs": [
            {"k": "鞋面", "v": "皮革拼接"},
            {"k": "中底", "v": "泡棉缓震"},
            {"k": "鞋型", "v": "低帮板鞋经典轮廓"},
        ],
        "selling_points": [
            {"title": "百搭鞋型", "detail": "街头/通勤/穿搭出片率极高"},
            {"title": "配色文化", "detail": "联名与稀有配色炒价，话题度常年在线"},
            {"title": "入门友好", "detail": "基础款价格亲民，潮流入门首选"},
        ],
    },
    {
        "industry": "服饰穿搭",
        "category_tags": ["时尚穿搭", "好物测评"],
        "brand": "波司登",
        "name": "极寒系列羽绒服",
        "series": "专业保暖 · 国民品牌",
        "headline": "极寒环境的中国答案：高蓬松鹅绒 + 三重锁温，-30℃ 也不虚",
        "price_range": "约 1500-3000 元",
        "specs": [
            {"k": "填充", "v": "高蓬松度鹅绒"},
            {"k": "锁温", "v": "三重保暖系统（羽绒 + 蓄热内里）"},
            {"k": "面料", "v": "防风防水面料"},
            {"k": "适用", "v": "-30℃ 极寒环境"},
        ],
        "selling_points": [
            {"title": "国货羽绒第一品牌", "detail": "登峰/极寒系列多次伴随中国科考"},
            {"title": "真材实料", "detail": "高蓬松鹅绒，保暖不臃肿"},
            {"title": "价格带全", "detail": "千元到高端都有，覆盖大众到户外党"},
        ],
    },
    # ---------------- 家用电器 ----------------
    {
        "industry": "家用电器",
        "category_tags": ["好物测评", "生活记录", "时尚穿搭"],
        "brand": "戴森",
        "name": "Supersonic 吹风机",
        "series": "HD 系列 · 高速吹风机开创者",
        "headline": "吹风机界的奢侈品：高速数码马达 + 智能温控，快干不伤发",
        "price_range": "约 2990-3690 元",
        "specs": [
            {"k": "马达", "v": "V9 数码马达（转速 11 万转/分）"},
            {"k": "风速", "v": "Air Multiplier 气流倍增技术"},
            {"k": "温控", "v": "每秒 40 次智能测温"},
        ],
        "selling_points": [
            {"title": "品类开创者", "detail": "重新定义吹风机，高价仍热销的标杆"},
            {"title": "快干不伤发", "detail": "大风量低温度，护发人群信仰充值"},
            {"title": "送礼硬通货", "detail": "礼赠场景辨识度极高"},
        ],
    },
    {
        "industry": "家用电器",
        "category_tags": ["好物测评", "生活记录"],
        "brand": "添可",
        "name": "芙万洗地机",
        "series": "智能洗地机开创者",
        "headline": "拖地革命：吸拖洗一体 + 恒压活水，干湿垃圾一遍净",
        "price_range": "约 1500-3000 元",
        "specs": [
            {"k": "功能", "v": "吸尘 + 拖地 + 自清洁一体"},
            {"k": "清洁", "v": "恒压活水系统（滚刷自清洁）"},
            {"k": "续航", "v": "单次约 35 分钟"},
        ],
        "selling_points": [
            {"title": "品类代名词", "detail": "提起洗地机就想到芙万，心智占位强"},
            {"title": "省时省力", "detail": "顽固污渍一遍过，拖完即自清洁"},
            {"title": "智能化语音", "detail": "脏污自识别，屏幕语音提示状态"},
        ],
    },
]


async def main() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as db:
        added = 0
        skipped = 0
        for p in PRODUCTS:
            exists = (
                await db.execute(
                    select(M.Product).where(
                        M.Product.brand == p["brand"],
                        M.Product.name == p["name"],
                        M.Product.status == "active",
                    )
                )
            ).scalar_one_or_none()
            if exists is not None and exists.headline == p["headline"]:
                skipped += 1
                print(f"[product] 跳过（一致）: {p['brand']} {p['name']}")
                continue
            if exists is not None:
                # 内容有更新：老版本归档，新版本重建（保持幂等可重入）
                exists.status = "archived"
            db.add(M.Product(**p))
            added += 1
            print(f"[product] 写入: {p['brand']} {p['name']}（{p['industry']}）")
        await db.commit()
        print(f"\n完成：新增/更新 {added}，跳过 {skipped}。")


if __name__ == "__main__":
    asyncio.run(main())
