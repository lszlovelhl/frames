"""运行时配置（无需重启即生效的小型设置）。

为什么不用数据库表：这些是"用户偏好"级别的设置（如创作台用哪个模型档），
放 JSON 文件即可，避免为一个开关引入 alembic 迁移。文件位置：
`backend/data/runtime_config.json`，随 data 目录一起备份。

当前管理的设置：
- creation_model：创作台（对话/引导/成稿/继续）使用的模型档位，默认 free_flash
- breakdown_model：五层拆解使用的模型档位，默认 free_flash
  （档位为逻辑名，实际指向哪家服务商由「模型管理」页的 provider priority 决定）
- billing_enforced：是否强制点数校验（扣点拦截），默认 False = 免计费模式。
  真实充值渠道未上线前默认放开点数校验；充值上线后把此项改为 true 即可切回强制计费。
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from app.core.config import BACKEND_DIR

logger = logging.getLogger(__name__)

CONFIG_PATH = BACKEND_DIR / "data" / "runtime_config.json"

# 逻辑档位 → 内部 alias（ai.py 的 ENV_MODELS / provider kind 一致）
ROUTING_CHOICES: list[dict[str, str]] = [
    {
        "key": "free_flash",
        "alias": "flash",
        "label": "免费档（默认）",
        "hint": "优先走免费/低价模型（如 glm-4-flash、qwen-turbo），质量已按统一契约对齐",
    },
    {
        "key": "paid_pro",
        "alias": "pro",
        "label": "进阶档",
        "hint": "更强推理模型（如 glm-4-plus），耗时更长；需服务商余额充足",
    },
]
_CHOICE_KEYS = {c["key"]: c for c in ROUTING_CHOICES}
DEFAULT_CREATION_MODEL = "free_flash"
DEFAULT_BREAKDOWN_MODEL = "free_flash"

# 计费开关默认值：False = 免计费模式（放开点数校验，仅记用量/流水）
DEFAULT_BILLING_ENFORCED = False


def _read() -> dict[str, Any]:
    try:
        if CONFIG_PATH.exists():
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:  # noqa: BLE001
        logger.warning("runtime_config 读取失败，使用默认值", exc_info=True)
    return {}


def _write(data: dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def alias_for(choice_key: str | None, default: str = DEFAULT_CREATION_MODEL) -> str:
    """逻辑档位 → 内部 alias；非法值回退默认（保证永远指向免费档）。"""
    c = _CHOICE_KEYS.get(choice_key or "")
    if c:
        return c["alias"]
    return _CHOICE_KEYS[default]["alias"]


def get_routing() -> dict[str, str]:
    data = _read()
    return {
        "creation_model": data.get("creation_model") or DEFAULT_CREATION_MODEL,
        "breakdown_model": data.get("breakdown_model") or DEFAULT_BREAKDOWN_MODEL,
    }


def set_routing(
    *, creation_model: str | None = None, breakdown_model: str | None = None
) -> dict[str, str]:
    data = _read()
    if creation_model:
        if creation_model not in _CHOICE_KEYS:
            raise ValueError(f"未知档位：{creation_model}")
        data["creation_model"] = creation_model
    if breakdown_model:
        if breakdown_model not in _CHOICE_KEYS:
            raise ValueError(f"未知档位：{breakdown_model}")
        data["breakdown_model"] = breakdown_model
    _write(data)
    return get_routing()


def get_billing() -> dict[str, bool]:
    """计费开关当前值（供 /api/* 展示）。"""
    return {"billing_enforced": billing_enforced()}


def billing_enforced() -> bool:
    """是否强制点数校验：False = 免计费模式（默认），True = 强制计费。

    取值优先级（一处配置即可切换）：
    1) 环境变量 FRAMES_BILLING_ENFORCED（1/true/yes/on → 强制计费；0/false/no/off → 免计费）
    2) data/runtime_config.json 的 "billing_enforced"（改文件即生效，无需重启）
    3) 默认 False（免计费模式）
    """
    env = os.getenv("FRAMES_BILLING_ENFORCED")
    if env is not None and env.strip():
        return env.strip().lower() in {"1", "true", "yes", "on"}
    return bool(_read().get("billing_enforced", DEFAULT_BILLING_ENFORCED))


def set_billing_enforced(enforced: bool) -> dict[str, bool]:
    """写入免计费/强制计费开关（充值渠道上线后置 True 即可）。"""
    data = _read()
    data["billing_enforced"] = bool(enforced)
    _write(data)
    return get_billing()


def creation_alias() -> str:
    """创作台当前应使用的 alias（默认免费档）。"""
    return alias_for(_read().get("creation_model"), DEFAULT_CREATION_MODEL)


def breakdown_alias() -> str:
    """五层拆解当前应使用的 alias（默认免费档）。"""
    return alias_for(_read().get("breakdown_model"), DEFAULT_BREAKDOWN_MODEL)


def describe(alias: str) -> str:
    for c in ROUTING_CHOICES:
        if c["alias"] == alias:
            return c["label"]
    return alias


def public_config() -> dict[str, Any]:
    r = get_routing()
    return {
        "routing": r,
        "choices": ROUTING_CHOICES,
        "creation_alias": alias_for(r["creation_model"]),
        "breakdown_alias": alias_for(r["breakdown_model"]),
        "billing_enforced": billing_enforced(),
        "config_path": str(Path(CONFIG_PATH)),
    }
