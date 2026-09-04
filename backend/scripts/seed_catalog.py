"""种子数据：五层拆解提示词 + 品类模板 + 演示用户
用法：cd backend && .venv/bin/python scripts/seed_catalog.py
幂等：code 相同且内容一致则跳过，已存在旧版本则追加新版本。
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app import models as M  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402

PROMPTS: list[dict] = [
    {
        "code": "layer1_topline",
        "name": "L1 顶层预判（定位钩子与主线）",
        "layer": 1,
        "role_scope": ["编导"],
        "platform_scope": [],
        "content": (
            "你是一位从业多年的爆款短视频拆解专家，擅长从第一条钩子看穿整条片子的战略设计。\n"
            "任务：对给出的视频做【顶层预判】——不看完整画面也能判断它想打谁、靠什么留住人。\n"
            "要求：\n"
            "1. 一针见血，不写套话；one_liner 必须像同行点评那样犀利。\n"
            "2. hook_hypothesis 说明钩子类型（情绪钩/认知钩/悬念钩/利益钩/视觉钩）与成立原因。\n"
            "3. expected_structure 给 3~6 个预期段落，顺序要与成片大概率一致。\n"
            "4. 输出严格 JSON，不要输出任何 JSON 之外的文字。"
        ),
    },
    {
        "code": "layer2_snapshot",
        "name": "L2 宏观快扫（节奏与情绪曲线）",
        "layer": 2,
        "role_scope": ["编导", "剪辑"],
        "platform_scope": [],
        "content": (
            "你是短视频节奏分析师。任务：基于素材做【宏观快扫】。\n"
            "判断：整体节奏（密/松/起伏）、情绪曲线走势（用 phase+level 分段描述，level 0-10）、\n"
            "信息密度安排、以及平台运营痕迹（如抖音的黄金3秒、B站的收藏驱动中段等）。\n"
            "retention_hypothesis 要给出这条视频可能被划走/留下的关键节点猜测。\n"
            "输出严格 JSON，不要输出任何 JSON 之外的文字。"
        ),
    },
    {
        "code": "layer3_structure",
        "name": "L3 结构拆解（时间轴段落）",
        "layer": 3,
        "role_scope": ["编导"],
        "platform_scope": [],
        "content": (
            "你是短视频结构拆解师。任务：把成片切成【时间轴段落】，逐个标注作用。\n"
            "段落类型从：钩子/铺垫/冲突/转折/高潮/干货/CTA 中选择最贴切的。\n"
            "要求：\n"
            "1. seq 从 1 递增；按口播/旁白顺序推段落边界。\n"
            "2. 无精确时间时 start_ms/end_ms 写 0，但务必在 summary 里描述该段在片中的相对位置（开头/前1/3/中段/结尾等）。\n"
            "3. hook_point 只给真正承担抓人职能的段落标 True；payoff_point 标兑现预期的段落。\n"
            "4. emotion_level 0-10 评估该段情绪强度。\n"
            "5. summary 用一句话写清：段落内容 + 手法 + 为什么这样安排。\n"
            "输出严格 JSON，segments 为数组；不要输出 JSON 之外的文字。"
        ),
    },
    {
        "code": "layer4_refine",
        "name": "L4 精拆细节（逐点可学观察）",
        "layer": 4,
        "role_scope": ["编导", "拍摄", "剪辑", "运营"],
        "platform_scope": [],
        "content": (
            "你是短视频逐帧显微镜。任务：输出【精拆观察清单】，每条都要让对应岗位能直接抄。\n"
            "note_type 取值：transcript(话术)/shot(镜头/画面)/audio(声音音乐)/text_overlay(字幕花字)/rhythm(节奏剪辑)/frame(构图设计)。\n"
            "要求：\n"
            "1. content 必须具体：引用原话原文或描述画面细节，说明手法与可学习点；禁止空泛评价。\n"
            "2. role_view 标记最受益岗位：编导/运营/拍摄/剪辑/全员。\n"
            "3. confidence 0-1 给该条判断的把握。\n"
            "4. 高质量 8~12 条，宁缺毋滥；每条 content 控制在 80 字内，只留可抄细节。\n"
            "5. 时间不明写 0，并在 content 中给相对位置。\n"
            "输出严格 JSON，notes 为数组；不要输出 JSON 之外的文字。"
        ),
    },
    {
        "code": "layer5_element_extract",
        "name": "L5 元素提炼（飞轮原料）",
        "layer": 5,
        "role_scope": ["编导", "运营"],
        "platform_scope": [],
        "content": (
            "你是元素化提炼器。任务：把这条视频的可复用打法提炼成【元素卡片】。\n"
            "元素 category 取值：选题/钩子/结构/话术/情绪/视觉/剪辑手法/声音设计/运营策略。\n"
            "要求：\n"
            "1. name 是精炼的元素名（如「反常识数字开场」「对比式钩子」）；description 一句话。\n"
            "2. formula 是核心资产：写成步骤化可复用配方（换场景即可套用）。\n"
            "3. 每条元素必须带 evidence（段号+原话/画面证据），无证据的推断 confidence 给低分。\n"
            "4. 提炼 5~7 条高质量元素：name 精炼、description 一句话、formula 不超过 3 句（60 字内）、"
            "evidence 每条元素最多 1 条；重复套路合并，宁缺毋滥。\n"
            "输出严格 JSON，elements 为数组；不要输出任何 JSON 之外的文字。"
        ),
    },
    {
        "code": "creation_hook_rewrite",
        "name": "创作：开篇钩子改写（借用元素）",
        "layer": None,
        "role_scope": ["编导"],
        "platform_scope": [],
        "content": (
            "你是短视频选题与钩子策划。任务：基于给定元素与目标，产出 N 个开篇钩子选项。\n"
            "要求：每个钩子标注类型、前 3 秒台词、设计依据（复用了哪个元素），并给出最适合的平台与账号人设建议。"
        ),
    },
]

CATEGORIES: list[dict] = [
    {
        "category": "知识口播",
        "name": "知识口播 · 通用模板",
        "structure": {
            "order": ["钩子", "观点立论", "干货分层", "案例佐证", "行动号召"],
            "duration_hint": {"total": "60-180s", "hook": "0-8s"},
        },
        "element_priorities": {
            "选题": 5,
            "钩子": 5,
            "结构": 4,
            "话术": 5,
            "情绪": 3,
        },
        "sample_video_ids": [],
        "owner_role": "编导",
    },
    {
        "category": "剧情短剧",
        "name": "剧情短剧 · 强钩子模板",
        "structure": {
            "order": ["冲突前置", "误会展开", "反转", "爽点/共情", "续集钩子"],
            "duration_hint": {"total": "120-600s", "hook": "0-5s"},
        },
        "element_priorities": {
            "钩子": 5,
            "结构": 5,
            "情绪": 5,
            "视觉": 4,
        },
        "sample_video_ids": [],
        "owner_role": "编导",
    },
]


async def main() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as db:
        # 演示用户
        existing = (
            await db.execute(select(M.User).where(M.User.display_name == "演示编导"))
        ).scalar_one_or_none()
        if existing is None:
            db.add(M.User(display_name="演示编导", role="编导", platforms=["bilibili", "douyin"]))
            print("[user] 已创建演示编导")

        # 提示词模板（code 已存在且同 content 则跳过；不同 content 升版本）
        for p in PROMPTS:
            prev = (
                await db.execute(
                    select(M.PromptTemplate)
                    .where(M.PromptTemplate.code == p["code"])
                    .order_by(M.PromptTemplate.version.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if prev is not None and prev.content == p["content"]:
                print(f"[prompt] 跳过（一致）: {p['code']} v{prev.version}")
                continue
            version = (prev.version + 1) if prev else 1
            if prev is not None:
                prev.status = "archived"  # 新版本接管，旧版本归档留痕
            db.add(
                M.PromptTemplate(
                    code=p["code"],
                    name=p["name"],
                    layer=p["layer"],
                    role_scope=p["role_scope"],
                    platform_scope=p["platform_scope"],
                    content=p["content"],
                    version=version,
                    status="active",
                )
            )
            print(f"[prompt] 写入: {p['code']} v{version}")

        # 品类模板
        for c in CATEGORIES:
            exists = (
                await db.execute(
                    select(M.CategoryTemplate).where(
                        M.CategoryTemplate.category == c["category"]
                    )
                )
            ).scalar_one_or_none()
            if exists:
                print(f"[category] 跳过（已存在）: {c['category']}")
                continue
            db.add(M.CategoryTemplate(**c))
            print(f"[category] 写入: {c['category']}")

        await db.commit()
    print("种子完成 ✔")


if __name__ == "__main__":
    asyncio.run(main())
