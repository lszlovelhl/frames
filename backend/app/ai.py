"""AI 模型网关 — 统一调用入口（多服务商集合版）

业务代码只关心档位：flash / pro / vision（或任意模型名）。
网关从 ai_providers 表（api-key 集合）按 priority 选择已启用、带 key、
且含对应 kind 模型的服务商完成请求；未配置任何 provider 时回退 .env 的
DEEPSEEK_* 旧配置（provider 记 deepseek），保证老链路不坏。

每次调用自动向 ai_usage_logs 落一笔用量（独立会话 + 失败重试 1 次；
重试仍失败记 ERROR 级日志，不阻塞主流程 —— 见 docs/03-development-log.md 遗留风险修复）。
"""
import asyncio
import logging

import httpx

from app.core.config import (
    AI_PRICE_IN_CNY_PER_M,
    AI_PRICE_OUT_CNY_PER_M,
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL_FLASH,
    DEEPSEEK_MODEL_PRO,
    DEEPSEEK_MODEL_VISION,
)

logger = logging.getLogger(__name__)

# 旧 env 回退档位
ENV_MODELS: dict[str, str] = {
    "flash": DEEPSEEK_MODEL_FLASH,
    "pro": DEEPSEEK_MODEL_PRO,
    "vision": DEEPSEEK_MODEL_VISION,
}


def _short_body(resp: httpx.Response, limit: int = 300) -> str:
    """截断服务商返回的错误正文，便于排障（如余额不足/参数超限）。"""
    try:
        return resp.text[:limit].replace("\n", " ")
    except Exception:  # noqa: BLE001
        return ""


async def _usage_cost_cny(alias: str, usage: dict | None) -> float | None:
    """按 alias 档位单价估算成本（元）。未知档位返回 None，避免误导。"""
    if not usage:
        return None
    p_in = AI_PRICE_IN_CNY_PER_M.get(alias)
    p_out = AI_PRICE_OUT_CNY_PER_M.get(alias)
    if p_in is None or p_out is None:
        return None
    pt = int(usage.get("prompt_tokens") or 0)
    ct = int(usage.get("completion_tokens") or 0)
    return round((pt * p_in + ct * p_out) / 1_000_000, 6)


USAGE_LOG_RETRY_DELAY = 0.2  # 秒：SQLite 并发写偶发 "database is locked" 后的重试间隔
USAGE_LOG_MAX_ATTEMPTS = 2  # 首次 + 重试 1 次（与原「仅告警」相比：不再静默丢账）


async def _log_usage(
    *,
    provider: str,
    alias: str,
    model_name: str | None,
    scene: str,
    ref_type: str | None,
    ref_id: str | None,
    usage: dict | None,
    ok: bool,
    error: str | None = None,
) -> None:
    """独立会话写 ai_usage_logs。

    失败（如 SQLite 并发 `database is locked`）时退避重试 1 次；
    重试仍失败则记 ERROR 级日志（含 scene/ref 便于对账补偿），不上抛、不阻塞业务。
    """
    from uuid import UUID

    from app import models as M
    from app.db import SessionLocal

    ref_uuid = None
    if ref_id:
        try:
            ref_uuid = UUID(ref_id)
        except ValueError:
            ref_uuid = None
    cost = await _usage_cost_cny(alias, usage)

    for attempt in range(1, USAGE_LOG_MAX_ATTEMPTS + 1):
        try:
            async with SessionLocal() as session:
                row = M.AiUsageLog(
                    provider=provider[:32],
                    scene=scene,
                    ref_type=ref_type,
                    ref_id=ref_uuid,
                    alias=alias,
                    model=model_name,
                    prompt_tokens=int(usage.get("prompt_tokens") or 0) if usage else 0,
                    completion_tokens=int(usage.get("completion_tokens") or 0)
                    if usage
                    else 0,
                    total_tokens=int(usage.get("total_tokens") or 0) if usage else 0,
                    cost_cny=cost,
                    ok=ok,
                    error=error,
                )
                session.add(row)
                await session.commit()
            if attempt > 1:
                logger.info("AI usage 记账重试成功（第 %s 次）：scene=%s", attempt, scene)
            return
        except Exception as exc:  # noqa: BLE001 用量记账失败不应影响业务
            if attempt < USAGE_LOG_MAX_ATTEMPTS:
                logger.warning(
                    "记录 AI usage 失败，%.2fs 后重试：scene=%s error=%s",
                    USAGE_LOG_RETRY_DELAY,
                    scene,
                    exc,
                )
                await asyncio.sleep(USAGE_LOG_RETRY_DELAY)
                continue
            logger.error(
                "记录 AI usage 失败（重试后仍失败，本条用量未落库，请按 scene/ref 对账）: "
                "scene=%s provider=%s ref_type=%s ref_id=%s error=%s",
                scene,
                provider,
                ref_type,
                ref_id,
                exc,
                exc_info=True,
            )


async def _resolve_target(
    model: str,
    prefer_provider: str | None,
) -> tuple[str, str, str, str]:
    """返回 (provider_key, base_url, api_key, model_id)。

    优先 provider 表配置；无可用则回退 env DeepSeek。
    未知档位 model 按原样模型名走（kind 精确匹配时传别名）。
    """
    from app.db import SessionLocal
    from app.services.ai_provider import resolve_alias

    async with SessionLocal() as session:
        hit = await resolve_alias(session, model, prefer_provider=prefer_provider)
        if hit:
            p_key, model_id, base_url = hit
            from sqlalchemy import select

            from app import models as M

            p = (
                await session.execute(
                    select(M.AiProvider).where(M.AiProvider.key == p_key).limit(1)
                )
            ).scalar_one_or_none()
            if p and p.api_key:
                # 顺带把该服务商余额自动刷新一次（低频；失败静默）
                try:
                    from app.services.ai_provider import auto_refresh_balance

                    await auto_refresh_balance(session, p)
                except Exception:  # noqa: BLE001
                    pass
                return p.key, p.base_url, p.api_key, model_id

    # 回退 env DeepSeek
    if DEEPSEEK_API_KEY:
        model_id = ENV_MODELS.get(model, model)
        return "deepseek", DEEPSEEK_BASE_URL, DEEPSEEK_API_KEY, model_id
    raise RuntimeError(
        "未配置可用的 AI 服务商：请在「模型管理」页填入至少一家服务商的 api-key"
    )


async def chat(
    messages: list[dict],
    model: str = "flash",
    temperature: float | None = None,
    max_tokens: int | None = None,
    json_mode: bool = False,
    timeout: float = 180,
    scene: str = "misc",
    ref_type: str | None = None,
    ref_id: str | None = None,
    provider: str | None = None,
) -> dict:
    """调用任一已接入服务商的 Chat Completions（OpenAI 兼容）。

    返回 {reply, reasoning, model, usage, provider}
    scene/ref_type/ref_id 仅用于用量记账归类，不影响请求本身。
    provider 为 None 时按 priority 自动路由；显式传入则固定该服务商。
    """
    provider_key, base_url, api_key, model_id = await _resolve_target(
        model, prefer_provider=provider
    )
    url = base_url.rstrip("/") + "/chat/completions"

    payload: dict = {"model": model_id, "messages": messages, "stream": False}
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens:
        payload["max_tokens"] = max_tokens
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code >= 400:
                # 带上服务商返回的错误正文（如 "Insufficient Balance" / "max_tokens 超限"），
                # 否则调用方只能看到干巴巴的 400，无法定位
                raise httpx.HTTPStatusError(
                    f"{resp.status_code} {resp.reason_phrase} "
                    f"{_short_body(resp)} | model={model_id} provider={provider_key}",
                    request=resp.request,
                    response=resp,
                )
            data = resp.json()

        choice = data["choices"][0]["message"]
        result = {
            "reply": choice.get("content", ""),
            "reasoning": choice.get("reasoning_content"),
            "model": data.get("model"),
            "usage": data.get("usage"),
            "provider": provider_key,
            # finish_reason=length 表示被 max_tokens 截断，上层据此触发续写补全
            "finish_reason": data["choices"][0].get("finish_reason"),
        }
        await _log_usage(
            provider=provider_key,
            alias=model,
            model_name=result["model"],
            scene=scene,
            ref_type=ref_type,
            ref_id=ref_id,
            usage=result["usage"],
            ok=True,
        )
        return result
    except Exception as exc:  # noqa: BLE001
        await _log_usage(
            provider=provider_key,
            alias=model,
            model_name=None,
            scene=scene,
            ref_type=ref_type,
            ref_id=ref_id,
            usage=None,
            ok=False,
            error=str(exc)[:500],
        )
        raise
