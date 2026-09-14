"""点数计费路由 /api/billing/*"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.services import billing

router = APIRouter(prefix="/api/billing", tags=["billing"])


class RechargeReq(BaseModel):
    package: str  # light / creator / team


@router.get("/account")
async def account(db: AsyncSession = Depends(get_session)):
    """点数账户总览 + 套餐 + 最近流水。"""
    return await billing.account_payload(db)


@router.post("/recharge")
async def recharge(req: RechargeReq, db: AsyncSession = Depends(get_session)):
    """模拟充值到账（本地无真实支付渠道）。"""
    await billing.recharge(db, req.package)
    await db.commit()
    return await billing.account_payload(db)


@router.post("/free-claim")
async def free_claim(db: AsyncSession = Depends(get_session)):
    """领取免费体验 100 点（限一次）。"""
    await billing.free_claim(db)
    await db.commit()
    return await billing.account_payload(db)
