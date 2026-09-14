"""点数计费服务 — 预检 / 扣点 / 充值 / 免费体验 / 流水

设计对齐 docs/05-points-billing.md：
- 动作前预检余额，不足抛 INSUFFICIENT_POINTS（HTTP 402）
- AI 动作成功完成后才扣点（失败不扣）
- 日消费上限防脚本打穿
- 免费体验 100 点限一次
- 无真实支付渠道：充值为「模拟到账」，接口预留真实支付扩展位

免计费模式（FREE MODE，默认开启）：
- 真实充值渠道上线前，`runtime_config` 的 billing_enforced 默认 False，此时
  `precheck` 不做余额/日上限校验、`consume` 只记用量流水不扣余额，
  创作台/拆解/AI 对话等不再被「点数不足」拦截；AI 真实调用与用量账照常记录。
- 充值上线后把该开关置 True（一处配置）即恢复强制计费，无需改业务代码。
"""
import logging
import uuid
from datetime import datetime, time, timedelta

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as M
from app.services import runtime_config

logger = logging.getLogger(__name__)

# 套餐（点数 / 元）
PACKAGES: dict[str, dict] = {
    "light": {"name": "轻量", "points": 700, "amount_cny": 29},
    "creator": {"name": "创作", "points": 2000, "amount_cny": 79},
    "team": {"name": "团队", "points": 5000, "amount_cny": 149},
}

# 动作 → 点数
ACTION_POINTS: dict[str, int] = {
    "breakdown_short": 100,
    "breakdown_long": 300,
    "guide_generate": 150,
    "chat": 10,
}

# 长视频阈值（默认 10 分钟）
LONG_VIDEO_MS = 10 * 60 * 1000

# 单账户每日消费点数上限（防脚本打穿）
DAILY_CONSUME_LIMIT = 3000

# 免费体验点数
FREE_GRANT_POINTS = 100

# 免计费模式下记流水用的交易类型（不计入「今日已用」与日消费上限）
FREE_TX_TYPE = "free_usage"


def billing_enforced() -> bool:
    """是否强制点数校验：False = 免计费模式（默认）。

    单一开关来源：runtime_config.billing_enforced（含环境变量覆盖）。
    """
    return runtime_config.billing_enforced()


def action_for_video(duration_ms: int | None) -> str:
    """按视频时长选拆解档位。"""
    if duration_ms is None or duration_ms < LONG_VIDEO_MS:
        return "breakdown_short"
    return "breakdown_long"


def _insufficient(required: int, balance: int) -> HTTPException:
    return HTTPException(
        status_code=402,
        detail={
            "code": "INSUFFICIENT_POINTS",
            "message": "点数不足，请充值后再试",
            "required_points": required,
            "balance_points": balance,
        },
    )


def _daily_limit() -> HTTPException:
    return HTTPException(
        status_code=429,
        detail={
            "code": "DAILY_LIMIT",
            "message": f"今日点数消费已达上限（{DAILY_CONSUME_LIMIT} 点），请明天再试",
        },
    )


async def _default_user_id(db: AsyncSession) -> uuid.UUID | None:
    """当前单机模式：取第一个 user 作为默认账户主体。"""
    row = (
        await db.execute(select(M.User.id).order_by(M.User.created_at).limit(1))
    ).scalar_one_or_none()
    return row


async def get_or_create_account(db: AsyncSession) -> M.CreditAccount:
    """获取默认点数账户；不存在则创建（自动赠送免费体验 100 点）。"""
    user_id = await _default_user_id(db)
    acc = (
        await db.execute(
            select(M.CreditAccount)
            .where(M.CreditAccount.user_id == user_id)
            .order_by(M.CreditAccount.created_at)
            .limit(1)
        )
    ).scalar_one_or_none()
    if acc is not None:
        return acc
    acc = M.CreditAccount(user_id=user_id, balance_points=0)
    db.add(acc)
    await db.flush()
    # 免费体验自动发放
    tx = M.CreditTransaction(
        account_id=acc.id,
        type="free_grant",
        action="free_claim",
        points=FREE_GRANT_POINTS,
        amount_cny=0,
        note="免费体验 100 点",
    )
    acc.balance_points += FREE_GRANT_POINTS
    acc.free_claimed = True
    db.add(tx)
    await db.flush()
    logger.info("created credit account %s with free grant", acc.id)
    return acc


async def _today_consumed(db: AsyncSession, account_id: uuid.UUID) -> int:
    start = datetime.combine(datetime.now().date(), time.min)
    total = (
        await db.execute(
            select(func.coalesce(func.sum(M.CreditTransaction.points), 0)).where(
                M.CreditTransaction.account_id == account_id,
                M.CreditTransaction.type == "consume",
                M.CreditTransaction.created_at >= start,
            )
        )
    ).scalar_one()
    return -int(total) if total else 0


async def precheck(db: AsyncSession, action: str) -> tuple[M.CreditAccount, int]:
    """动作前预检：返回 (账户, 所需点数)。

    强制计费：余额不足抛 402 INSUFFICIENT_POINTS，超日上限抛 429 DAILY_LIMIT。
    免计费模式（默认）：只解析动作与账户，不做任何拦截，直接放行。
    """
    points = ACTION_POINTS.get(action)
    if points is None:
        raise HTTPException(status_code=400, detail=f"未知计费动作: {action}")
    acc = await get_or_create_account(db)
    if not billing_enforced():
        # 免计费模式：放开点数校验（账户仍取一次，供后续 consume 记用量流水）
        return acc, points
    if acc.balance_points < points:
        raise _insufficient(points, acc.balance_points)
    today = await _today_consumed(db, acc.id)
    if today + points > DAILY_CONSUME_LIMIT:
        raise _daily_limit()
    return acc, points


async def consume(
    db: AsyncSession,
    *,
    account: M.CreditAccount,
    action: str,
    points: int,
    ref_type: str | None = None,
    ref_id=None,
    note: str = "",
) -> M.CreditTransaction:
    """成功后记流水。调用方负责业务成功后触发。

    强制计费：扣减余额并累加 total_consumed_points（type="consume"）。
    免计费模式：仅记用量流水（type="free_usage"，points 记为负数作用量口径），
    不动余额与累计消耗，也不参与日消费上限统计。
    """
    if billing_enforced():
        if account.balance_points < points:
            raise _insufficient(points, account.balance_points)
        tx_type = "consume"
        note_text = note or action
        account.balance_points -= points
        account.total_consumed_points += points
    else:
        tx_type = FREE_TX_TYPE
        note_text = f"{note or action}（免计费模式，未扣点）"
    tx = M.CreditTransaction(
        account_id=account.id,
        type=tx_type,
        action=action,
        points=-points,
        ref_type=ref_type,
        ref_id=ref_id,
        note=note_text,
    )
    db.add(tx)
    await db.flush()
    return tx


async def recharge(db: AsyncSession, package_key: str) -> M.CreditTransaction:
    """模拟充值到账：按套餐加余额并记流水。

    TODO(真实支付)：接入微信/支付宝后，此处改为「创建待支付订单 → 回调确认到账」，
    新增 provider/trade_no 字段即可，接口签名不变。
    """
    pkg = PACKAGES.get(package_key)
    if pkg is None:
        raise HTTPException(status_code=400, detail=f"未知套餐: {package_key}")
    acc = await get_or_create_account(db)
    tx = M.CreditTransaction(
        account_id=acc.id,
        type="recharge",
        action=f"pkg_{package_key}",
        points=pkg["points"],
        amount_cny=pkg["amount_cny"],
        note=f"{pkg['name']}套餐 {pkg['amount_cny']} 元 / {pkg['points']} 点",
    )
    acc.balance_points += pkg["points"]
    acc.total_recharged_points += pkg["points"]
    db.add(tx)
    await db.flush()
    return tx


async def free_claim(db: AsyncSession) -> M.CreditTransaction | None:
    """免费体验 100 点（限一次）。已有账户但未领取也可补领。"""
    acc = await get_or_create_account(db)
    if acc.free_claimed:
        raise HTTPException(status_code=400, detail="免费体验仅限领取一次")
    tx = M.CreditTransaction(
        account_id=acc.id,
        type="free_grant",
        action="free_claim",
        points=FREE_GRANT_POINTS,
        amount_cny=0,
        note="免费体验 100 点",
    )
    acc.balance_points += FREE_GRANT_POINTS
    acc.free_claimed = True
    db.add(tx)
    await db.flush()
    return tx


async def account_payload(db: AsyncSession) -> dict:
    """账户总览（供 UsageView 顶部点数区块）。"""
    acc = await get_or_create_account(db)
    recent = (
        await db.execute(
            select(M.CreditTransaction)
            .where(M.CreditTransaction.account_id == acc.id)
            .order_by(M.CreditTransaction.created_at.desc())
            .limit(20)
        )
    ).scalars().all()
    today = await _today_consumed(db, acc.id)
    free_usage = (
        await db.execute(
            select(func.coalesce(func.sum(M.CreditTransaction.points), 0)).where(
                M.CreditTransaction.account_id == acc.id,
                M.CreditTransaction.type == FREE_TX_TYPE,
            )
        )
    ).scalar_one()
    enforced = billing_enforced()
    return {
        # 计费模式（免计费模式下列出的 action_points 仍为价目参考，不实际扣点）
        "billing": {
            "enforced": enforced,
            "mode": "enforced" if enforced else "free",
            "free_used_points": -int(free_usage or 0),
            "note": (
                "强制计费：点数不足将被拦截"
                if enforced
                else "免计费模式：点数校验已放开，AI 用量照常记录"
            ),
        },
        "account": {
            "id": str(acc.id),
            "balance_points": acc.balance_points,
            "total_recharged_points": acc.total_recharged_points,
            "total_consumed_points": acc.total_consumed_points,
            "free_claimed": acc.free_claimed,
            "today_consumed": today,
            "daily_limit": DAILY_CONSUME_LIMIT,
        },
        "packages": [
            {
                "key": k,
                "name": v["name"],
                "points": v["points"],
                "amount_cny": v["amount_cny"],
            }
            for k, v in PACKAGES.items()
        ],
        "action_points": [
            {"action": k, "points": v} for k, v in ACTION_POINTS.items()
        ],
        "recent_transactions": [
            {
                "id": str(t.id),
                "type": t.type,
                "action": t.action,
                "points": t.points,
                "amount_cny": t.amount_cny,
                "ref_type": t.ref_type,
                "note": t.note,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in recent
        ],
    }
