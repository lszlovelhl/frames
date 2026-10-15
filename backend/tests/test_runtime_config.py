"""runtime_config 纯逻辑测试：档位映射 / 计费开关。

通过 monkeypatch 模块级 CONFIG_PATH 指向 tmp_path，绝不读写真实
backend/data/runtime_config.json，不读 .env。
"""
from __future__ import annotations

import json

import pytest

from app.services import runtime_config


@pytest.fixture
def config_path(monkeypatch, tmp_path):
    p = tmp_path / "runtime_config.json"
    monkeypatch.setattr(runtime_config, "CONFIG_PATH", p)
    return p


def _write(p, data):
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_alias_for_known_and_fallback():
    assert runtime_config.alias_for("free_flash") == "flash"
    assert runtime_config.alias_for("paid_pro") == "pro"
    assert runtime_config.alias_for("no_such_key") == "flash"
    assert runtime_config.alias_for(None) == "flash"


def test_default_routing(config_path):
    routing = runtime_config.get_routing()
    assert routing == {"creation_model": "free_flash", "breakdown_model": "free_flash"}


def test_routing_reads_from_file(config_path):
    _write(config_path, {"creation_model": "paid_pro", "breakdown_model": "free_flash"})
    routing = runtime_config.get_routing()
    assert routing["creation_model"] == "paid_pro"
    assert runtime_config.creation_alias() == "pro"
    assert runtime_config.breakdown_alias() == "flash"


def test_set_routing_valid_and_invalid(config_path):
    _write(config_path, {})
    runtime_config.set_routing(creation_model="paid_pro")
    assert runtime_config.creation_alias() == "pro"
    with pytest.raises(ValueError):
        runtime_config.set_routing(creation_model="unknown_tier")


def test_billing_enforced_default_false(monkeypatch, config_path):
    monkeypatch.delenv("FRAMES_BILLING_ENFORCED", raising=False)
    _write(config_path, {})
    assert runtime_config.billing_enforced() is False


def test_billing_enforced_from_file(config_path):
    _write(config_path, {"billing_enforced": True})
    assert runtime_config.billing_enforced() is True


def test_billing_enforced_env_overrides(monkeypatch, config_path):
    _write(config_path, {"billing_enforced": False})
    monkeypatch.setenv("FRAMES_BILLING_ENFORCED", "true")
    assert runtime_config.billing_enforced() is True
    monkeypatch.setenv("FRAMES_BILLING_ENFORCED", "0")
    assert runtime_config.billing_enforced() is False


def test_set_billing_writes_file(config_path):
    runtime_config.set_billing_enforced(True)
    assert runtime_config.billing_enforced() is True
    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert data["billing_enforced"] is True


def test_describe_known_alias():
    assert "进阶档" in runtime_config.describe("pro")
    assert "免费档" in runtime_config.describe("flash")
