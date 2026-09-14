"""视频内容赛道自动分类服务

轻量 flash 调用：根据标题/作者/标签/简介，从赛道全集里选一个最贴切的标签。
用于建档后自动回填 video.category_guess，支撑拆解库 / 元素库按内容赛道分组。
分类失败只记日志，不阻塞建档与拆解主流程。
"""
import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app import models as M
from app.ai import chat
from app.core.categories import CATEGORY_TAXONOMY, normalize_category

logger = logging.getLogger(__name__)

_CLASSIFY_SYSTEM = (
    "你是短视频内容运营专家。下面给出一条短视频的标题、作者与标签，"
    "请判断它属于哪种内容赛道，从候选列表中选一个最贴切的返回。"
    f"候选赛道（只能返回其中之一，不要解释）：{'、'.join(CATEGORY_TAXONOMY)}。"
    "如确实无法判断（信息太少/太杂），返回：无法判断。"
    "输出 JSON：{\"category\": \"赛道名\"}"
)


async def classify_category(
    *,
    title: str = "",
    author: str = "",
    tags: list[str] | None = None,
    desc: str = "",
) -> str:
    """对单条视频返回赛道中文标签；失败/无法判断返回 ""。"""
    tags = tags or []
    probe = "｜".join(
        [x for x in [title.strip(), author.strip(), desc.strip(), " ".join(tags).strip()] if x]
    )
    if not probe:
        return ""
    probe = probe[:600]
    try:
        resp = await chat(
            messages=[
                {"role": "system", "content": _CLASSIFY_SYSTEM},
                {"role": "user", "content": f"标题/作者/标签：{probe}"},
            ],
            model="flash",
            temperature=0,
            max_tokens=64,
            json_mode=True,
            timeout=30,
            scene="video_category_classify",
        )
        raw = (resp.get("reply") or "").strip()
        cat = _parse_category_reply(raw)
        if not cat:
            # reasoning 模型可能把结论放 reasoning_content（reply 为空）→ 从推理文本兜底
            reason = (resp.get("reasoning") or "").strip()
            cat = _parse_category_reply(reason)
        if not cat and ("无法判断" in raw or "无法判断" in (resp.get("reasoning") or "")):
            return ""
        return cat
    except Exception:  # noqa: BLE001
        logger.warning("赛道分类失败 title=%r", title[:60], exc_info=True)
        return ""


def _parse_category_reply(raw: str) -> str:
    """容错解析模型回复中的赛道标签（不要求严格合法 JSON）。"""
    import re

    if not raw:
        return ""
    text = raw.strip().strip("`").strip()
    # 1) 标准 JSON {"category": "xxx"}
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return normalize_category(str(data.get("category") or ""))
    except Exception:  # noqa: BLE001
        pass
    # 2) 半截 JSON / 带前后说明：抽取 category 字段值
    m = re.search(r'"category"\s*:\s*"([^"]+)"', text)
    if m:
        return normalize_category(m.group(1))
    # 3) 裸标签：去掉多余说明后整体归一
    for token in re.split(r"[，,。；;：:\n]+", text):
        if token and ("无法判断" in token or "不确定" in token):
            return ""
        c = normalize_category(token)
        if c:
            return c
    return normalize_category(text)


async def backfill_missing_categories(db: AsyncSession, limit: int = 200) -> dict:
    """对 category_guess 为空（或等于空串）的存量视频补分类；返回处理统计。"""
    from sqlalchemy import or_, select

    rows = (
        (
            await db.execute(
                select(M.Video)
                .where(
                    or_(
                        M.Video.category_guess.is_(None),
                        M.Video.category_guess == "",
                    )
                )
                .order_by(M.Video.created_at.asc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    done = 0
    skipped = 0
    for v in rows:
        cat = await classify_category(
            title=v.title or "",
            author=v.author_name or "",
            tags=v.tags or [],
        )
        if not cat:
            skipped += 1
            continue
        v.category_guess = cat
        await db.flush()
        done += 1
        logger.info("回填赛道 video=%s title=%r -> %s", str(v.id)[:8], (v.title or "")[:40], cat)
    await db.commit()
    return {"total": len(rows), "filled": done, "skipped": skipped}
