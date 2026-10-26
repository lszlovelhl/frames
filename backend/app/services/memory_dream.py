"""记忆结晶服务（Auto-Dream）。

受 QwenPaw/ReMe 的 Auto-Dream 机制启发：
定期读取用户的聊天记录，用 LLM 提炼出稳定的偏好/事实，
写入 user_memory 长期记忆表。去冗存精，记忆越用越准。

触发方式：
1. 定时任务（推荐每天 23:00）
2. 启动时检查上次运行时间，超过 24 小时自动补跑
3. 手动调用
"""
import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy import text

logger = logging.getLogger(__name__)

# 记忆结晶的模型：用 flash 档省钱，提炼不需要大模型
DREAM_MODEL = "flash"
# 每次最多处理的天数
DREAM_WINDOW_DAYS = 3


async def dream_consolidate(db, user_id: str | None = None) -> dict:
    """把最近 DREAM_WINDOW_DAYS 天的聊天记录结晶成长期记忆。

    Args:
        db: 数据库 session
        user_id: 指定用户；None 则处理所有有聊天的用户

    Returns:
        {"users": n, "memories_added": m, "errors": [...]}
    """
    try:
        # 1. 找要处理的用户（有聊天记录的用户）
        if user_id:
            user_ids = [user_id]
        else:
            rows = (await db.execute(text(
                "SELECT DISTINCT user_id FROM chat_sessions WHERE updated_at >= "
                "datetime('now', '-' || :days || ' days')"
            ), {"days": DREAM_WINDOW_DAYS})).fetchall()
            user_ids = [r[0] for r in rows]

        stats = {"users": len(user_ids), "memories_added": 0, "errors": []}

        for uid in user_ids:
            try:
                added = await _consolidate_one(db, uid)
                stats["memories_added"] += added
            except Exception as e:
                logger.warning("记忆结晶失败 user=%s: %s", uid, e)
                stats["errors"].append(str(e)[:100])

        return stats
    except Exception as e:
        logger.error("记忆结晶整体失败: %s", e)
        return {"users": 0, "memories_added": 0, "errors": [str(e)[:100]]}


async def _consolidate_one(db, user_id: str) -> int:
    """结晶单个用户：读聊天 → LLM 提炼 → 写入 user_memory。返回新增条数。"""
    # 1. 读最近 N 天的用户消息
    rows = (await db.execute(text(
        "SELECT content FROM chat_messages WHERE session_id IN "
        "(SELECT id FROM chat_sessions WHERE user_id=:uid) "
        "AND role='user' AND created_at >= datetime('now', '-' || :days || ' days') "
        "ORDER BY created_at DESC LIMIT 50"
    ), {"uid": user_id, "days": DREAM_WINDOW_DAYS})).fetchall()

    if not rows:
        return 0

    # 2. 拼接对话文本
    msgs = "\n".join(f"- {r[0][:200]}" for r in rows)
    if len(msgs) > 6000:
        msgs = msgs[-6000:]

    # 3. LLM 提炼稳定的偏好/事实
    from app.services.two_layer.pipeline import call_flash_llm

    prompt = (
        "以下是用户最近的对话内容，请提炼出「稳定的用户偏好和事实」，"
        "用于长期记忆。要求：\n"
        "1. 只提炼稳定的偏好/事实（如职业、内容偏好、使用习惯），忽略一次性请求\n"
        "2. 每条一句话，简洁具体\n"
        "3. 最多输出 5 条，没有稳定信息就输出空列表\n"
        "4. 输出 JSON 格式：{\"memories\": [\"条目1\", \"条目2\"]}\n"
        f"\n对话内容：\n{msgs}"
    )
    try:
        resp = await call_flash_llm(
            prompt=prompt,
            system="你是记忆提炼助手，从对话中提取值得长期记住的用户信息，输出 JSON。",
            max_tokens=300,
        )
    except Exception as e:
        logger.warning("记忆结晶 LLM 调用失败 user=%s: %s", user_id, e)
        return 0

    # 4. 解析 JSON 并写入
    import json as _json
    import re as _re

    added = 0
    memories = []
    try:
        m = _re.search(r'\{.*\}', resp, _re.DOTALL)
        if m:
            data = _json.loads(m.group(0))
            memories = data.get("memories", [])
    except Exception:
        # JSON 解析失败，退化为逐行解析
        memories = [l.strip().lstrip("- ").strip() for l in resp.splitlines() if l.strip()]

    for line in memories:
        line = (line or "").strip()
        if not line or len(line) < 4 or len(line) > 120:
            continue

        # 去重：已有相似记忆就不重复写
        key = line[:4]
        exists = (await db.execute(text(
            "SELECT id FROM user_memory WHERE user_id=:uid AND content LIKE :k"
        ), {"uid": user_id, "k": f"%{key}%"})).fetchone()
        if exists:
            continue

        mem_id = uuid.uuid4().hex
        await db.execute(text(
            "INSERT INTO user_memory (id, user_id, memory_type, content, importance) "
            "VALUES (:mid, :uid, 'dream', :c, 6)"
        ), {"mid": mem_id, "uid": user_id, "c": line})

        # 同步向量化（失败不影响记忆写入）
        try:
            from app.services.embedding import embed_texts, upsert_one
            vecs = await embed_texts([line])
            await upsert_one(db, "user_memory", mem_id, line, vecs[0])
        except Exception as e:
            logger.warning("结晶记忆向量化失败（不影响写入）：%s", e)

        added += 1

    if added:
        await db.commit()
        logger.info("记忆结晶完成 user=%s 新增 %s 条", user_id, added)
    return added


async def maybe_run_dream(db) -> dict:
    """启动时检查：超过 24 小时没结晶就补跑。返回结晶结果。"""
    try:
        # 查上次结晶时间（用一张 meta 表记录，没有就创建）
        await db.execute(text(
            "CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT)"
        ))
        row = (await db.execute(text(
            "SELECT value FROM app_meta WHERE key='last_dream'"
        ))).fetchone()

        if row:
            last = datetime.fromisoformat(row[0])
            if datetime.now() - last < timedelta(hours=24):
                return {"skipped": True, "reason": "24 小时内已结晶过"}

        # 执行结晶
        stats = await dream_consolidate(db)

        # 记录时间
        await db.execute(text(
            "INSERT INTO app_meta (key, value) VALUES ('last_dream', :v) "
            "ON CONFLICT(key) DO UPDATE SET value=:v"
        ), {"v": datetime.now().isoformat()})
        await db.commit()

        return stats
    except Exception as e:
        logger.error("maybe_run_dream 失败: %s", e)
        return {"skipped": True, "reason": f"异常: {str(e)[:100]}"}


async def run_dream_background() -> dict:
    """独立后台入口：自己开 session 跑结晶，供 chat 接口异步触发。"""
    try:
        from app.db import SessionLocal
        async with SessionLocal() as db:
            return await maybe_run_dream(db)
    except Exception as e:
        logger.error("run_dream_background 失败: %s", e)
        return {"skipped": True, "reason": f"异常: {str(e)[:100]}"}
