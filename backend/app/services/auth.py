"""本地账号体系：注册/登录/登出/校验。

设计原则（隐私安全优先）：
- 密码 bcrypt 哈希存储，绝不存明文
- 登录态用随机 opaque token（非 JWT），存 users.auth_token
- 全部数据在本机 SQLite，不上云
- 幂等迁移：users 表缺列自动补（PRAGMA 检查 + ALTER）
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

import bcrypt
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = {
    "username": "VARCHAR(64)",
    "password_hash": "VARCHAR(128)",
    "auth_token": "VARCHAR(64)",
}


async def ensure_user_schema(db: AsyncSession) -> None:
    """幂等迁移：检查 users 表列，缺则 ALTER 补上。"""
    cols = {r[1] for r in (await db.execute(text("PRAGMA table_info(users)"))).all()}
    for name, decl in REQUIRED_COLUMNS.items():
        if name not in cols:
            await db.execute(text(f"ALTER TABLE users ADD COLUMN {name} {decl}"))
            logger.info("users 表补列：%s", name)
    # username 唯一索引（幂等）
    idx = (await db.execute(text(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name='ix_users_username'"
    ))).fetchone()
    if not idx:
        await db.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_username ON users(username)"
        ))
    await db.commit()


# ==============================================================
# 密码
# ==============================================================
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


# ==============================================================
# 账号操作
# ==============================================================
async def register(db: AsyncSession, username: str, password: str, display_name: str = "") -> dict[str, Any]:
    """注册。成功返回 {ok, user_id, username, token}。"""
    username = username.strip()
    if len(username) < 2:
        return {"ok": False, "message": "用户名至少 2 个字符"}
    if len(password) < 6:
        return {"ok": False, "message": "密码至少 6 位"}

    exists = (await db.execute(text(
        "SELECT id FROM users WHERE username=:u"
    ), {"u": username})).fetchone()
    if exists:
        return {"ok": False, "message": "用户名已存在，换个名字试试"}

    uid = uuid.uuid4().hex
    token = uuid.uuid4().hex
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, auth_token, display_name, role, platforms) "
        "VALUES (:id, :u, :h, :t, :d, '全能', '[]')"
    ), {"id": uid, "u": username, "h": hash_password(password), "t": token, "d": display_name or username})
    await db.commit()
    logger.info("新用户注册：%s", username)
    return {"ok": True, "user_id": uid, "username": username, "token": token}


async def login(db: AsyncSession, username: str, password: str) -> dict[str, Any]:
    """登录。成功返回 {ok, user_id, username, token}。"""
    row = (await db.execute(text(
        "SELECT id, password_hash FROM users WHERE username=:u"
    ), {"u": username.strip()})).fetchone()
    if not row or not verify_password(password, row[1]):
        return {"ok": False, "message": "用户名或密码不对"}

    token = uuid.uuid4().hex
    await db.execute(text(
        "UPDATE users SET auth_token=:t WHERE id=:id"
    ), {"t": token, "id": row[0]})
    await db.commit()
    return {"ok": True, "user_id": row[0], "username": username.strip(), "token": token}


async def logout(db: AsyncSession, token: str) -> None:
    await db.execute(text(
        "UPDATE users SET auth_token=NULL WHERE auth_token=:t"
    ), {"t": token})
    await db.commit()


async def resolve_token(db: AsyncSession, token: str | None) -> dict[str, Any] | None:
    """按 token 找用户。返回 {id, username} 或 None。"""
    if not token:
        return None
    row = (await db.execute(text(
        "SELECT id, username FROM users WHERE auth_token=:t"
    ), {"t": token})).fetchone()
    if not row:
        return None
    return {"id": row[0], "username": row[1]}
