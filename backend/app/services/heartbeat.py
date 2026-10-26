"""心跳主动智能（Heartbeat）。

吸取 OpenClaw 经验：周期性"意识"——定期检查有没有需要关注的事，
有才主动告诉用户，没有就安静（NO_REPLY 原则）。

检查项：
1. 用户好久没来（>2 天）→ 主动问候 + 最近动态摘要
2. 记忆结晶兜底（>24h 没结晶且有新对话）→ 触发结晶，结晶出新记忆则主动告知
3. 去重：同一件事只发一次（app_meta 记 content hash）
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Any

from sqlalchemy import text

from app.db import SessionLocal

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL = 1800  # 30 分钟一次
STALE_DAYS = 2  # 用户超过 2 天没来算"好久没来"
DREAM_HOURS = 24


async def heartbeat_loop(immediate: bool = False) -> None:
    """后台心跳主循环。immediate=True 时启动后立即检查一轮。"""
    if immediate:
        try:
            async with SessionLocal() as db:
                await check_and_notify(db)
        except Exception:
            logger.exception("心跳首轮检查失败")
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL)
        try:
            async with SessionLocal() as db:
                await check_and_notify(db)
        except Exception:
            logger.exception("心跳检查失败（不影响主循环）")


# ==============================================================
# 检查与通知
# ==============================================================
async def check_and_notify(db) -> dict[str, Any]:
    """执行一轮心跳检查。返回本轮触发了什么。"""
    report = {"greet": 0, "dream": 0, "notified": 0}

    # 1. 找出所有有过对话的用户 + 其最近活跃 session
    sessions = (await db.execute(text(
        "SELECT cs.id AS sid, cs.user_id, cs.updated_at "
        "FROM chat_sessions cs "
        "JOIN (SELECT user_id, MAX(updated_at) AS last FROM chat_sessions GROUP BY user_id) t "
        "ON cs.user_id=t.user_id AND cs.updated_at=t.last"
    ))).all()
    if not sessions:
        return report

    now = time.time()

    for sid, uid, updated_at in sessions:
        # 转时间戳（SQLite CURRENT_TIMESTAMP 格式 'YYYY-MM-DD HH:MM:SS'，按本地时区）
        last_ts = _to_ts(str(updated_at))
        if last_ts is None:
            continue

        # 1.1 好久没来 → 主动问候（去重：每个用户每 7 天最多一次）
        days_gone = (now - last_ts) / 86400
        if days_gone >= STALE_DAYS:
            greeted = await _mark_once(db, uid, "heartbeat_greet", f"greet@{int(days_gone)}")
            if greeted:
                msg = await _build_greet(db, uid)
                if msg:
                    await _insert_message(db, sid, msg, "heartbeat")
                    report["greet"] += 1
                    report["notified"] += 1

        # 1.2 记忆结晶兜底：>24h 没结晶且有对话 → 结晶，新记忆主动告知
        dream_due = await _dream_due(db, uid, last_ts)
        if dream_due:
            new_mems = await _run_dream(db, uid)
            if new_mems:
                memo = await _mark_once(db, uid, "heartbeat_dream", ",".join(new_mems[:3]))
                if memo and len(new_mems) >= 3:
                    msg = "💭 我安静地想了想最近和你的对话，提炼了" \
                          f"{len(new_mems)} 条长期记忆：\n" + "\n".join(f"• {m}" for m in new_mems[:3])
                    await _insert_message(db, sid, msg, "heartbeat")
                    report["dream"] += len(new_mems)
                    report["notified"] += 1

    if report["notified"]:
        logger.info("心跳：问候 %s 个用户，结晶 %s 条记忆，主动消息 %s 条",
                    report["greet"], report["dream"], report["notified"])
    return report


# ==============================================================
# 内部工具
# ==============================================================
async def _build_greet(db, uid: str) -> str | None:
    """构造"好久不见"问候：最近拆解数 + 记得的事。"""
    try:
        stats = (await db.execute(text(
            "SELECT COUNT(*) FROM breakdown_jobs bj "
            "JOIN chat_sessions cs ON bj.id=cs.last_job_id "
            "WHERE cs.user_id=:uid AND bj.status='done'"
        ), {"uid": uid})).fetchone()
        done = stats[0] if stats else 0

        mem = (await db.execute(text(
            "SELECT content FROM user_memory WHERE user_id=:uid "
            "ORDER BY updated_at DESC LIMIT 1"
        ), {"uid": uid})).fetchone()

        parts = ["👋 好久不见！"]
        if done:
            parts.append(f"你上次走的时候已经拆了 {done} 个视频了。")
        if mem:
            parts.append(f"我还记得你跟我提过：{mem[0]}")
        parts.append("有什么新想法，随时来找我。")
        return "\n".join(parts)
    except Exception:
        return None


async def _run_dream(db, uid: str) -> list[str]:
    """执行一次记忆结晶，返回新提炼的记忆列表（空=没有新的）。"""
    try:
        from app.services.memory_dream import run_dream_background
        # 复用后台任务逻辑但同步拿结果
        from app.services.memory_dream import maybe_run_dream
        result = await maybe_run_dream(db)
        # maybe_run_dream 返回 dict；拿新增记忆
        if isinstance(result, dict):
            added = result.get("added", 0)
            if added:
                rows = (await db.execute(text(
                    "SELECT content FROM user_memory WHERE user_id=:uid "
                    "AND memory_type='dream' ORDER BY created_at DESC LIMIT :n"
                ), {"uid": uid, "n": added})).fetchall()
                return [r[0] for r in rows]
        return []
    except Exception as e:
        logger.warning("心跳结晶失败：%s", e)
        return []


async def _dream_due(db, uid: str, last_ts: float) -> bool:
    """判断记忆结晶是否到期。"""
    try:
        row = (await db.execute(text(
            "SELECT value FROM app_meta WHERE key=:k"
        ), {"k": "last_dream_time"})).fetchone()
        if row and row[0]:
            return time.time() - float(row[0]) > DREAM_HOURS * 3600
        return True  # 从没结晶过 → 到期
    except Exception:
        return True


async def _mark_once(db, uid: str, key_prefix: str, value: str) -> bool:
    """去重标记：同 key 已存在则返回 False（不重复发）。"""
    key = f"{key_prefix}:{uid}"
    h = hashlib.md5(value.encode()).hexdigest()[:12]
    try:
        row = (await db.execute(text(
            "SELECT value FROM app_meta WHERE key=:k"
        ), {"k": key})).fetchone()
        if row and row[0] == h:
            return False
        await db.execute(text(
            "INSERT INTO app_meta (key, value) VALUES (:k, :v) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
        ), {"k": key, "v": h})
        await db.commit()
        return True
    except Exception:
        return True


async def _insert_message(db, session_id: str, content: str, intent: str) -> None:
    import uuid
    await db.execute(text(
        "INSERT INTO chat_messages (id, session_id, role, content, intent) "
        "VALUES (:mid, :sid, 'assistant', :content, :intent)"
    ), {"mid": uuid.uuid4().hex, "sid": session_id, "content": content, "intent": intent})
    await db.execute(text(
        "UPDATE chat_sessions SET updated_at=CURRENT_TIMESTAMP WHERE id=:sid"
    ), {"sid": session_id})
    await db.commit()


def _to_ts(s: str) -> float | None:
    """把 'YYYY-MM-DD HH:MM:SS' 转 unix 时间戳（本地时区）。"""
    try:
        from datetime import datetime
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").timestamp()
    except Exception:
        return None
