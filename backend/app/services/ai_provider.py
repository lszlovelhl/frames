"""AI 服务商（api-key 集合）管理服务。

- PROVIDER_TEMPLATES：预置各家服务商接入模板（设计期一次性接好接口，填 key 即用）
- resolve_alias：业务只传档位 alias，网关据此选择实际服务商+模型
- balance：DeepSeek / Moonshot 等提供官方余额查询的真实适配；不支持的在卡片标「手动」
"""
import logging
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as M

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------
# 预置服务商模板（OpenAI 兼容协议；模型/单价/充值链接为官方口径，供一键填入）
# ---------------------------------------------------------------
PROVIDER_TEMPLATES: dict[str, dict[str, Any]] = {
    "deepseek": {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "models": [
            {"id": "deepseek-chat", "kind": "flash", "label": "DeepSeek Chat (V3)"},
            {"id": "deepseek-reasoner", "kind": "pro", "label": "DeepSeek Reasoner (R1)"},
        ],
        "topup_url": "https://platform.deepseek.com/top_up",
        "brand_color": "#4D6BFE",
        "balance_api": {"method": "GET", "path": "/user/balance"},
    },
    "doubao": {
        "name": "火山方舟（豆包）",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "models": [
            {"id": "doubao-seed-1-6-250615", "kind": "flash", "label": "豆包 Seed 1.6"},
            {"id": "doubao-seed-1-6-thinking-250615", "kind": "pro", "label": "豆包 Seed Thinking"},
        ],
        "topup_url": "https://console.volcengine.com/ark/region:ark+cn-beijing/openManagement",
        "brand_color": "#00A6F0",
        "balance_api": None,
    },
    "moonshot": {
        "name": "Kimi (Moonshot)",
        "base_url": "https://api.moonshot.cn/v1",
        "models": [
            {"id": "kimi-k2-turbo-preview", "kind": "pro", "label": "Kimi K2 Turbo"},
            {"id": "moonshot-v1-8k", "kind": "flash", "label": "Moonshot V1 8K"},
        ],
        "topup_url": "https://platform.moonshot.cn/console/balance",
        "brand_color": "#1E1E1E",
        "balance_api": {"method": "GET", "path": "/users/me/balance"},
    },
    "qwen": {
        "name": "通义千问 (DashScope)",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": [
            {"id": "qwen3-max", "kind": "pro", "label": "Qwen3 Max"},
            {"id": "qwen-turbo-latest", "kind": "flash", "label": "Qwen Turbo"},
            {"id": "qwen-vl-max-latest", "kind": "vision", "label": "Qwen VL Max"},
        ],
        "topup_url": "https://bailian.console.aliyun.com/#/fund",
        "brand_color": "#615CED",
        "balance_api": None,
    },
    "zhipu": {
        "name": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": [
            {"id": "glm-4-flash", "kind": "flash", "label": "GLM-4-Flash"},
            {"id": "glm-4-plus", "kind": "pro", "label": "GLM-4-Plus"},
            {"id": "glm-4v-flash", "kind": "vision", "label": "GLM-4V-Flash"},
        ],
        "topup_url": "https://open.bigmodel.cn/console/overview",
        "brand_color": "#10A37F",
        "balance_api": None,
    },
    "openai": {
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "models": [
            {"id": "gpt-4o-mini", "kind": "flash", "label": "GPT-4o mini"},
            {"id": "gpt-4o", "kind": "pro", "label": "GPT-4o"},
            {"id": "gpt-4o-mini", "kind": "vision", "label": "GPT-4o (视觉)"},
        ],
        "topup_url": "https://platform.openai.com/settings/organization/billing",
        "brand_color": "#10A37F",
        "balance_api": {"method": "GET", "path": "/dashboard/billing/subscription"},
    },
}


def template(key: str) -> dict | None:
    t = PROVIDER_TEMPLATES.get(key)
    return dict(t) if t else None


# ---------------------------------------------------------------
# 查询与路由
# ---------------------------------------------------------------

async def list_providers(db: AsyncSession, include_disabled: bool = True) -> list[M.AiProvider]:
    stmt = select(M.AiProvider)
    if not include_disabled:
        stmt = stmt.where(M.AiProvider.enabled.is_(True))
    stmt = stmt.order_by(M.AiProvider.priority, M.AiProvider.created_at)
    return list((await db.execute(stmt)).scalars().all())


async def get_provider_by_key(db: AsyncSession, key: str) -> M.AiProvider | None:
    return (
        await db.execute(select(M.AiProvider).where(M.AiProvider.key == key).limit(1))
    ).scalar_one_or_none()


def provider_public(p: M.AiProvider) -> dict:
    return {
        "key": p.key,
        "name": p.name,
        "base_url": p.base_url,
        "api_key_set": bool(p.api_key),
        "api_key_masked": _mask_key(p.api_key),
        "models": p.models or [],
        "priority": p.priority,
        "enabled": p.enabled,
        "brand_color": p.brand_color,
        "logo_url": p.logo_url,
        "topup_url": p.topup_url,
        "balance_cny": p.balance_cny,
        "balance_checked_at": p.balance_checked_at.isoformat()
        if p.balance_checked_at
        else None,
        "balance_manual": p.balance_manual,
        "balance_warn_threshold": p.balance_warn_threshold,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


def _mask_key(k: str) -> str:
    if not k:
        return ""
    if len(k) <= 8:
        return "*" * len(k)
    return f"{k[:3]}···{k[-4:]}"


async def resolve_alias(
    db: AsyncSession, alias: str, prefer_provider: str | None = None
) -> tuple[str, str, str] | None:
    """返回 (provider_key, model_id, base_url)；无可用配置返回 None。

    按 provider.priority 取首个已启用、带 key、且含 kind 匹配模型的 provider。
    kind=other 的模型作为任意档位兜底。
    """
    for p in await list_providers(db, include_disabled=False):
        if prefer_provider and p.key != prefer_provider:
            continue
        if not p.api_key:
            continue
        # 余额守卫：已知余额 ≤0 的服务商（如未充值的 DeepSeek）调用必然 400，
        # 直接跳过改用后续可用的免费服务商，避免"创作台/拆解突然全线失败"
        if (
            not prefer_provider
            and p.balance_cny is not None
            and p.balance_cny <= 0
        ):
            logger.info("跳过余额不足的服务商 %s（余额 %.4f），改用后续可用服务商", p.key, p.balance_cny)
            continue
        exact = next(
            (m for m in (p.models or []) if m.get("kind") == alias), None
        )
        if exact is not None:
            return p.key, exact["id"], p.base_url
        # kind=other 兜底（手动添加的任意模型）
        fallback = next(
            (m for m in (p.models or []) if m.get("kind") == "other"), None
        )
        if fallback is not None:
            return p.key, fallback["id"], p.base_url
    if prefer_provider:
        # 指定了 provider 但没匹配到 → 再全量找一次该 provider 首个模型
        p = await get_provider_by_key(db, prefer_provider)
        if p and p.api_key and p.models:
            return p.key, p.models[0]["id"], p.base_url
    return None


# ---------------------------------------------------------------
# 余额真实查询（按服务商能力；不支持返回 None）
# ---------------------------------------------------------------

async def fetch_balance(p: M.AiProvider) -> tuple[float | None, str | None]:
    """返回 (余额元, 备注)；失败返回 (None, 原因)。"""
    tpl = template(p.key)
    spec = (tpl or {}).get("balance_api")
    if not spec or not p.api_key:
        return None, "该服务商暂不支持接口查询余额，可在卡片手动维护"
    headers = {
        "Authorization": f"Bearer {p.api_key}",
        "Content-Type": "application/json",
    }
    url = p.base_url.rstrip("/") + spec["path"]
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.request(spec["method"], url, headers=headers)
            if resp.status_code == 401:
                return None, "api-key 无效或无余额权限"
            resp.raise_for_status()
            data = resp.json()
        bal = _parse_balance_data(p.key, data)
        if bal is None:
            return None, "余额字段解析失败，建议手动维护"
        return round(bal, 2), None
    except Exception as exc:  # noqa: BLE001
        return None, f"查询失败：{exc}"


def _parse_balance_data(key: str, data: dict) -> float | None:
    try:
        if key == "deepseek":
            # {"is_available": true, "balance_infos": [{"currency":"CNY","total_balance":"110.00",...}]}
            for info in data.get("balance_infos") or []:
                if info.get("currency") == "CNY":
                    return float(info.get("total_balance") or 0)
            return float((data.get("balance_infos") or [{}])[0].get("total_balance") or 0)
        if key == "moonshot":
            return float(data.get("available_balance") or data.get("balance") or 0)
        if key == "openai":
            return None
    except (TypeError, ValueError, IndexError):
        return None
    return None


async def auto_refresh_balance(db: AsyncSession, p: M.AiProvider) -> None:
    """自动查询并落库余额（仅支持接口查询的服务商；手动维护的不动）。"""
    if p.balance_manual or not p.api_key:
        return
    tpl = template(p.key)
    if not tpl or not tpl.get("balance_api"):
        return
    bal, _ = await fetch_balance(p)
    if bal is not None:
        p.balance_cny = bal
        p.balance_checked_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(p)


# ---------------------------------------------------------------
# 低余额预警判定
# ---------------------------------------------------------------

def balance_status(p: M.AiProvider) -> str:
    """ok / low / unknown / no_key"""
    if not p.api_key:
        return "no_key"
    if p.balance_cny is None:
        return "unknown"
    if p.balance_cny <= p.balance_warn_threshold:
        return "low"
    return "ok"
