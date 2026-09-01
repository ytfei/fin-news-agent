"""LangChain 模型工厂单测（不发起真实请求）。"""
import pytest

from fin_news.agents.llm.client import LLMUnavailable
from fin_news.agents.llm.factory import ModelFactory
from fin_news.core.config import Settings


def _settings(**kw) -> Settings:
    base = dict(
        volcengine_api_key="vk",
        deepseek_api_key="dk",
        llm_default_provider="volcengine",
        llm_fallback_provider="deepseek",
        embedding_provider="volcengine",
    )
    base.update(kw)
    return Settings(_env_file=None, **base)


def test_build_returns_none_without_api_key():
    factory = ModelFactory(_settings(volcengine_api_key=""))
    assert factory.build("volcengine", "scoring") is None


def test_build_caches_client_instance():
    factory = ModelFactory(_settings())
    a = factory.build("volcengine", "scoring")
    b = factory.build("volcengine", "scoring")
    assert a is b


def test_chat_uses_fallback_chain():
    model = ModelFactory(_settings()).chat("scoring")
    # with_fallbacks 返回 RunnableWithFallbacks
    assert type(model).__name__ == "RunnableWithFallbacks"


def test_chat_without_fallback_returns_plain_model():
    model = ModelFactory(_settings()).chat("scoring", with_fallback=False)
    assert type(model).__name__ == "ChatOpenAI"


def test_chat_falls_back_when_primary_missing():
    model = ModelFactory(_settings(volcengine_api_key="")).chat("scoring")
    assert type(model).__name__ == "ChatOpenAI"  # 只剩 deepseek，直接返回


def test_chat_raises_when_no_credentials():
    with pytest.raises(LLMUnavailable):
        ModelFactory(_settings(volcengine_api_key="", deepseek_api_key="")).chat("scoring")


def test_temperature_differs_by_role():
    factory = ModelFactory(_settings())
    scoring = factory.build("volcengine", "scoring")
    qa = factory.build("volcengine", "qa")
    assert scoring.temperature < qa.temperature


def test_structured_returns_runnable():
    from fin_news.agents.schemas import ScoreBatchModel

    runnable = ModelFactory(_settings()).structured("scoring", ScoreBatchModel, include_raw=True)
    assert runnable is not None


def test_structured_raises_without_credentials():
    from fin_news.agents.schemas import ScoreBatchModel

    with pytest.raises((LLMUnavailable, Exception)):
        ModelFactory(_settings(volcengine_api_key="", deepseek_api_key="")).structured(
            "scoring", ScoreBatchModel
        )


def test_embeddings_requires_api_key():
    with pytest.raises(LLMUnavailable):
        ModelFactory(_settings(volcengine_api_key="")).embeddings()


def test_embeddings_client_built_and_cached():
    factory = ModelFactory(_settings())
    emb = factory.embeddings()
    assert emb is factory.embeddings()
    # 火山模型名不在 tiktoken 词表，必须关闭长度校验
    assert emb.check_embedding_ctx_length is False
