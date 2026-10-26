"""graph memory 实体关系（Mem0 2026 entity linking 精神，本地轻量落地）。

从记忆/素材内容中提取实体（城市/视频类型/平台/主题），建立
用户 ↔ 实体 的关系（关注/偏好），存入 memory_entities 表。
后续可用于：个性化推荐、问答加权、素材关联——让记忆不再是孤立文本，
而是一张可关联的关系网。

- 提取：本地词表（类型/平台/城市/品类）+ 通用正则（专名），免费不出机
- 关系：user_id ↔ entity，relation=关注/偏好，weight 随出现次数累积
- 接入点：记忆写入（remember / 演化流水线）后自动联动
"""

import logging
import re

from sqlalchemy import text as sa_text

logger = logging.getLogger(__name__)

# 领域词表：短视频拆解域的高价值实体（可按需扩充）
ENTITY_LISTS: dict[str, list[str]] = {
    "video_type": ["口播", "剧情", "vlog", "测评", "教程", "带货", "美食", "旅行", "知识", "情感",
                   "探店", "开箱", "游戏", "健身", "穿搭", "萌宠", "科技", "财经", "汽车", "亲子"],
    "platform": ["抖音", "bilibili", "B站", "哔哩哔哩", "YouTube", "快手", "小红书", "视频号"],
    "city": ["成都", "北京", "上海", "广州", "深圳", "杭州", "重庆", "西安", "长沙", "武汉", "南京", "苏州", "天津", "青岛"],
    "style": ["节奏快", "节奏慢", "治愈", "搞笑", "硬核", "专业", "接地气", "高清", "沉浸式", "第一人称"],
}

TYPE_LABELS = {"video_type": "视频类型", "platform": "平台", "city": "城市", "style": "风格"}

_ENTITIES_FLAT = [(e, t) for t, es in ENTITY_LISTS.items() for e in es]


async def ensure_graph_schema() -> None:
    """幂等：建 memory_entities 表。"""
    from app.db import engine

    async with engine.connect() as conn:
        await conn.execute(sa_text(
            "CREATE TABLE IF NOT EXISTS memory_entities ("
            " id TEXT PRIMARY KEY,"
            " user_id TEXT NOT NULL,"
            " entity TEXT NOT NULL,"
            " entity_type TEXT NOT NULL,"
            " relation TEXT NOT NULL DEFAULT '关注',"
            " weight REAL NOT NULL DEFAULT 1.0,"
            " created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,"
            " updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        ))
        await conn.commit()


def extract_entities(text: str) -> list[tuple[str, str]]:
    """从文本提取实体，返回 [(实体, 类型), ...]（去重，保序）。"""
    if not text:
        return []
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    low = text.lower()
    for ent, etype in _ENTITIES_FLAT:
        # 词表匹配（平台做大小写归一）
        key = ent.lower()
        if key in low and key not in seen:
            found.append((ent, etype))
            seen.add(key)
    # 通用专名：两位以上的连续中文品牌/栏目名暂不自动抓（避免噪声），
    # 领域词表已覆盖核心场景
    return found


async def _upsert_edge(db, user_id: str, entity: str, etype: str, weight_delta: float = 1.0) -> None:
    import uuid

    row = (await db.execute(sa_text(
        "SELECT id, weight FROM memory_entities "
        "WHERE user_id=:uid AND entity=:e AND entity_type=:t"
    ), {"uid": user_id, "e": entity, "t": etype})).fetchone()
    if row:
        await db.execute(sa_text(
            "UPDATE memory_entities SET weight=weight+:w, updated_at=CURRENT_TIMESTAMP WHERE id=:mid"
        ), {"w": weight_delta, "mid": row[0]})
    else:
        await db.execute(sa_text(
            "INSERT INTO memory_entities (id, user_id, entity, entity_type) VALUES (:mid, :uid, :e, :t)"
        ), {"mid": uuid.uuid4().hex, "uid": user_id, "e": entity, "t": etype})


async def link_from_memory(db, user_id: str, content: str) -> int:
    """记忆写入后联动：提取实体并建立用户↔实体关系。返回新增/更新条数。"""
    ents = extract_entities(content)
    for entity, etype in ents:
        await _upsert_edge(db, user_id, entity, etype)
    if ents:
        logger.info("graph memory：用户 %s 关联实体 %s", user_id[:8], [e for e, _ in ents])
    return len(ents)


async def get_user_entities(db, user_id: str, top_n: int = 20) -> list[dict]:
    """用户实体图谱（按 weight 排序）——个性化推荐/问答的图谱依据。"""
    rows = (await db.execute(sa_text(
        "SELECT entity, entity_type, weight FROM memory_entities "
        "WHERE user_id=:uid ORDER BY weight DESC, updated_at DESC LIMIT :n"
    ), {"uid": user_id, "n": top_n})).fetchall()
    return [{"entity": r[0], "type": r[1], "weight": r[2]} for r in rows]


async def entity_context(db, user_id: str, query: str, top_n: int = 5) -> str:
    """查询增强：query 命中用户实体时，返回相关实体上下文（供注入/加权）。"""
    ents = extract_entities(query)
    if not ents:
        return ""
    wanted = {e for e, _ in ents}
    user_ents = await get_user_entities(db, user_id, top_n=50)
    related = [u for u in user_ents if u["entity"] in wanted]
    if not related:
        return ""
    parts = [f"{u['entity']}({TYPE_LABELS.get(u['type'], u['type'])})" for u in related]
    return "、".join(parts)
