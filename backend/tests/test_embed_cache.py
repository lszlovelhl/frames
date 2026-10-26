"""验证 embedding 内存缓存：同内容只调一次 API。"""
import pytest

from app.services import embedding as emb


class FakeResponse:
    def raise_for_status(self):
        pass

    def json(self):
        # Ollama /api/embed 批量端点格式：{"embeddings": [[...], ...]}
        return {"embeddings": [[0.1, 0.2, 0.3]]}


@pytest.mark.asyncio
async def test_embed_cache_hits(monkeypatch):
    calls = {"n": 0}

    async def fake_post(self, url, headers=None, json=None):
        calls["n"] += 1
        return FakeResponse()

    monkeypatch.setattr("httpx.AsyncClient.post", fake_post)

    v1 = await emb.embed_texts(["同一句话"])
    v2 = await emb.embed_texts(["同一句话"])
    v3 = await emb.embed_texts(["同一句话"])

    assert v1 == v2 == v3
    assert calls["n"] == 1, f"应只调 1 次 API，实际 {calls['n']} 次"


@pytest.mark.asyncio
async def test_embed_cache_distinct(monkeypatch):
    calls = {"n": 0}

    async def fake_post(self, url, headers=None, json=None):
        calls["n"] += 1
        idx = calls["n"]
        return FakeResponse()

    monkeypatch.setattr("httpx.AsyncClient.post", fake_post)

    await emb.embed_texts(["第一句"])
    await emb.embed_texts(["第二句"])
    assert calls["n"] == 2
