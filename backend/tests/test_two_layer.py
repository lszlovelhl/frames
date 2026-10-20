"""两层 pipeline 与 harness 逻辑单元测试。"""
from __future__ import annotations

import pytest
from app.services.two_layer.pipeline import run_harness, run_pipeline


def test_run_harness_pads_missing_segment_and_emotion():
    l1_data = {
        "segments": [
            {
                "title": "开头",
                "start_sec": 0,
                "end_sec": 15,
                "purpose": "提出悬念",
                "summary": "画面开启",
            }
        ],
        "emotion_curve": [
            {"t_sec": 0, "intensity": 6.0, "direction": "紧张"},
            {"t_sec": 10, "intensity": 7.0, "direction": "兴奋"},
        ],
        "elements": [],
    }
    duration_sec = 60
    harnessed = run_harness(l1_data, duration_sec)

    # 验证补全了收尾段
    segments = harnessed["segments"]
    assert len(segments) == 2
    assert segments[-1]["title"] == "收尾"
    assert segments[-1]["start_sec"] == 15
    assert segments[-1]["end_sec"] == 60

    # 验证补全了情绪末尾点
    curve = harnessed["emotion_curve"]
    assert len(curve) == 3
    assert curve[-1]["t_sec"] == 60
    assert curve[-1]["intensity"] == 7.0


@pytest.mark.asyncio
async def test_run_pipeline_mocked(monkeypatch):
    async def mock_call_flash(*args, **kwargs):
        return """{
            "segments": [{"title": "第一段", "start_sec": 0, "end_sec": 30, "purpose": "引入", "summary": "做介绍"}],
            "emotion_curve": [{"t_sec": 0, "intensity": 5.0, "direction": "平静"}],
            "elements": [{"type": "hook", "name": "反问开头", "example": "你知道吗"}]
        }"""

    async def mock_call_pro(*args, **kwargs):
        return "# 完整成稿脚本\n\n这是测试的 Markdown 脚本内容。"

    monkeypatch.setattr("app.services.two_layer.pipeline.call_flash_llm", mock_call_flash)
    monkeypatch.setattr("app.services.two_layer.pipeline.call_pro_llm", mock_call_pro)

    res = await run_pipeline("转写文本", "画面描述", duration_sec=30, model="flash")
    assert res["l1_data"]["segments"][0]["title"] == "第一段"
    assert "# 完整成稿脚本" in res["markdown"]
