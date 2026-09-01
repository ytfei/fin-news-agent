"""Embedding 客户端（多模态向量化接口）单测，不发起真实请求。"""
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
