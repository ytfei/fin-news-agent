"""Embedding 客户端（多模态向量化接口）单测，不发起真实请求。"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from fin_news.agents.embeddings import DimensionMismatch, Embedder
from fin_news.agents.llm.client import LLMUnavailable
from fin_news.core.config import Settings


def _settings(**kw) -> Settings:
    # 显式指定 embedding 相关字段，避免被进程环境变量（source .env 后残留）污染
    base = dict(
        volcengine_api_key="vk",
        deepseek_api_key="dk",
        embedding_provider="volcengine",
        volcengine_base_url="https://ark.cn-beijing.volces.com/api/v3",
        embedding_dim=2048,
        volcengine_model_embedding="doubao-embedding-vision",
    )
    base.update(kw)
    # _env_file 是 pydantic-settings 的合法参数（test_config_settings.py 同款用法），
    # basedpyright 在本文件上下文中对其误报，故忽略
    return Settings(_env_file=None, **base)  # pyright: ignore[reportCallIssue]


def test_payload_uses_multimodal_object_input():
    embedder = Embedder(_settings())
    payload = embedder._payload("a")
    assert payload["model"] == "doubao-embedding-vision"
    # 关键差异：input 是对象数组，不是字符串数组
    assert payload["input"] == [{"type": "text", "text": "a"}]
    assert payload["dimensions"] == 2048
    assert payload["encoding_format"] == "float"
    assert payload["sparse_embedding"] == {"type": "disabled"}


def test_endpoint_is_multimodal_path():
    embedder = Embedder(_settings())
    url, headers = embedder._endpoint()
    assert url == "https://ark.cn-beijing.volces.com/api/v3/embeddings/multimodal"
    assert headers["Authorization"] == "Bearer vk"
    assert headers["Content-Type"] == "application/json"


def test_endpoint_raises_without_api_key():
    with pytest.raises(LLMUnavailable):
        Embedder(_settings(volcengine_api_key=""))._endpoint()


def test_parse_response_extracts_data_object_embedding():
    """火山 multimodal 响应的 data 是对象（非数组），embedding 直接挂在 data 下。"""
    vec = [0.1, 0.2, 0.3]
    data = {"id": "x", "data": {"embedding": vec, "object": "embedding"}}
    assert Embedder._parse_response(data) == vec


def test_validate_raises_on_dimension_mismatch():
    embedder = Embedder(_settings())
    with pytest.raises(DimensionMismatch):
        embedder._validate([[0.1] * 1024])  # 1024 != 配置的 2048


def test_validate_passes_on_match():
    embedder = Embedder(_settings())
    embedder._validate([[0.1] * 2048])  # 不抛异常即通过


# ----------------------------------------------------------------------
# 受限并发闸门与审计攒批（替换 _request，不发起真实 HTTP）
# ----------------------------------------------------------------------


def _vec(dim: int = 2048) -> list[float]:
    return [0.1] * dim


async def _fake_request(embedder, *, log: bool = False, **kw):
    """构造请求桩：需要审计记录时仍走真实 _log_call，保持被测链路完整。"""

    async def fake(text: str) -> list[float]:
        if log:
            model = embedder.settings.model_for(
                embedder.settings.embedding_provider, "embedding"
            )
            embedder._log_call(model=model, prompt_tokens=1, latency_ms=1, status="OK")
        return _vec()

    return fake


async def test_embed_concurrency_semaphore_caps_inflight():
    """进程级闸门：in-flight 请求数不超过 embedding_concurrency。"""
    embedder = Embedder(_settings(embedding_concurrency=3))
    active = 0
    peak = 0

    async def fake_request(text: str) -> list[float]:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.03)
        active -= 1
        return _vec()

    embedder._request = fake_request  # type: ignore[method-assign]
    vectors = await embedder.embed([f"t{i}" for i in range(15)], auto_flush=False)
    assert len(vectors) == 15
    assert peak <= 3


async def test_embed_waits_all_requests_then_raises_first_error():
    """单条失败不半途取消其余请求：全部结束后统一抛错（结果丢弃，状态干净）。"""
    embedder = Embedder(_settings())
    made = 0
    fails = {"t2", "t3"}

    async def fake_request(text: str) -> list[float]:
        nonlocal made
        made += 1
        await asyncio.sleep(0.005)
        if text in fails:
            raise RuntimeError("boom")
        return _vec()

    embedder._request = fake_request  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="boom"):
        await embedder.embed(["t1", "t2", "t3", "t4"], auto_flush=False)
    assert made == 4


async def test_embed_dimension_mismatch_still_terminates():
    """写入前维度校验仍在，维度不一致照旧整体抛错防污染索引。"""
    embedder = Embedder(_settings(embedding_dim=2048))

    async def fake_request(text: str) -> list[float]:
        return _vec(1024)

    embedder._request = fake_request  # type: ignore[method-assign]
    with pytest.raises(DimensionMismatch):
        await embedder.embed(["a"], auto_flush=False)


class _FakeScope:
    """模拟 db.session_scope 的 async 上下文管理器。"""

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):  # noqa: ANN002
        return None


def _patch_session(monkeypatch, session) -> None:
    monkeypatch.setattr("fin_news.agents.embeddings.session_scope", lambda: _FakeScope(session))


async def test_flush_logs_writes_pending_in_one_batch(monkeypatch):
    """审计攒批：N 条调用只在 flush_logs 时一次性 add_all 写库。"""
    embedder = Embedder(_settings())
    fake_session = AsyncMock()
    fake_session.add_all = MagicMock()  # 真实 AsyncSession.add_all 为同步方法
    _patch_session(monkeypatch, fake_session)
    embedder._request = await _fake_request(embedder, log=True)  # type: ignore[method-assign]

    # 批处理路径（auto_flush=False）：请求只进 pending，不落库
    await embedder.embed(["a", "b"], auto_flush=False)
    assert len(embedder._pending_logs) == 2

    await embedder.flush_logs()
    assert fake_session.add_all.call_count == 1
    added = fake_session.add_all.call_args[0][0]
    assert len(added) == 2
    assert embedder._pending_logs == []

    # 幂等：清空后再 flush 不再写库
    fake_session.reset_mock()
    await embedder.flush_logs()
    assert fake_session.add_all.call_count == 0


async def test_embed_one_still_flushes_immediately(monkeypatch):
    """embed_one（检索/QA 路径）保持即时写审计，行为与旧版一致。"""
    embedder = Embedder(_settings())
    fake_session = AsyncMock()
    fake_session.add_all = MagicMock()  # 真实 AsyncSession.add_all 为同步方法
    _patch_session(monkeypatch, fake_session)
    embedder._request = await _fake_request(embedder, log=True)  # type: ignore[method-assign]

    # embed_one 内部 auto_flush=True：单条结束即批量写一次（1 条 OK 日志）
    vec = await embedder.embed_one("查询文本")
    assert vec == _vec()
    assert fake_session.add_all.call_count == 1
    assert embedder._pending_logs == []
