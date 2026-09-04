"""DeepSeek 模型网关 — 统一调用入口
读取 backend/.env 中的 DEEPSEEK_* 配置
alias: flash / pro / vision → 实际模型名
"""
import httpx

from app.core.config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL_FLASH,
    DEEPSEEK_MODEL_PRO,
    DEEPSEEK_MODEL_VISION,
)

MODELS: dict[str, str] = {
    "flash": DEEPSEEK_MODEL_FLASH,
    "pro": DEEPSEEK_MODEL_PRO,
    "vision": DEEPSEEK_MODEL_VISION,
}


async def chat(
    messages: list[dict],
    model: str = "flash",
    temperature: float | None = None,
    max_tokens: int | None = None,
    json_mode: bool = False,
    timeout: float = 180,
) -> dict:
    """调用 DeepSeek Chat Completions。

    返回 {reply, reasoning, model, usage}
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

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

    choice = data["choices"][0]["message"]
    return {
        "reply": choice.get("content", ""),
        "reasoning": choice.get("reasoning_content"),
        "model": data.get("model"),
        "usage": data.get("usage"),
    }
