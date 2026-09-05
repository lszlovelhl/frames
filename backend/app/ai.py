"""DeepSeek 模型网关 — 统一调用入口
读取 backend/.env 中的 DEEPSEEK_* 配置
alias: flash / pro / vision → 实际模型名
每次调用自动向 ai_usage_logs 落一笔用量（独立会话，失败不阻塞主流程）。
"""
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

MODELS: dict[str, str] = {
    "flash": DEEPSEEK_MODEL_FLASH,
    "pro": DEEPSEEK_MODEL_PRO,
    "vision": DEEPSEEK_MODEL_VISION,
}


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


async def _log_usage(
    *,
    alias: str,
    model_name: str | None,
    scene: str,
    ref_type: str | None,
    ref_id: str | None,
    usage: dict | None,
    ok: bool,
    error: str | None = None,
) -> None:
    """独立会话写 ai_usage_logs；任何异常只告警不上抛。"""
    try:
        from uuid import UUID

        from app import models as M
        from app.db import SessionLocal

        cost = await _usage_cost_cny(alias, usage)
        ref_uuid = None
        if ref_id:
            try:
                ref_uuid = UUID(ref_id)
            except ValueError:
                ref_uuid = None
        async with SessionLocal() as session:
            row = M.AiUsageLog(
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
    except Exception:  # noqa: BLE001 用量记账失败不应影响业务
        logger.warning("记录 AI usage 失败", exc_info=True)


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
) -> dict:
    """调用 DeepSeek Chat Completions。

    返回 {reply, reasoning, model, usage}
    scene/ref_type/ref_id 仅用于用量记账归类，不影响请求本身。
    """
    if not DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置（backend/.env）")

    model_id = MODELS.get(model, model)  # 未知 alias 时按原始名传
    url = DEEPSEEK_BASE_URL.rstrip("/") + "/chat/completions"

    payload: dict = {"model": model_id, "messages": messages, "stream": False}
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens:
        payload["max_tokens"] = max_tokens
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}"}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

        choice = data["choices"][0]["message"]
        result = {
            "reply": choice.get("content", ""),
            "reasoning": choice.get("reasoning_content"),
            "model": data.get("model"),
            "usage": data.get("usage"),
        }
        await _log_usage(
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
