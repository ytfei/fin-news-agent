"""web_search 工具（Tavily SDK）测试。

主体是桩 client 单测，不发起真实网络请求（CI 可直接跑）。
另有一组 `live` 标记的端到端用例，会真实调用 Tavily、消耗额度，默认跳过：

    FIN_NEWS_LIVE_TESTS=1 TAVILY_API_KEY=tvly-xxx pytest -m live tests/test_web_search.py
"""
from __future__ import annotations

import os

import pytest
from tavily.errors import (
    BadRequestError,
    ForbiddenError,
    InvalidAPIKeyError,
    MissingAPIKeyError,
    UsageLimitExceededError,
)
from tavily.errors import TimeoutError as TavilyTimeoutError

from fin_news.agents.tools import web_search as ws
from fin_news.core.config import Settings

# 进程环境里可能有真实的 Tavily 配置，会污染「未配置」相关的断言
_ENV_KEYS = (
    "TAVILY_API_KEY",
    "WEB_SEARCH_API_KEY",
    "WEB_SEARCH_ENABLED",
    "WEB_SEARCH_BASE_URL",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def _settings(**kw) -> Settings:
    base = dict(web_search_enabled=True, web_search_api_key="tvly-test", web_search_base_url="")
    base.update(kw)
    return Settings(_env_file=None, **base)


class StubClient:
    """记录调用参数、回放预设响应的假 Tavily 客户端。"""

    def __init__(self, response=None, error=None):
        self.response = response if response is not None else {"results": []}
        self.error = error
        self.params: dict | None = None
        self.closed = False

    async def search(self, **params):
        self.params = params
        if self.error is not None:
            raise self.error
        return self.response

    async def close(self):
        self.closed = True


SAMPLE_RESPONSE = {
    "query": "央行降准",
    "results": [
        {
            "title": "华泰 | 固收：政策成为首要关注点",
            "url": "https://www.cls.cn/detail/123",
            "content": "正文" * 400,
            "score": 0.91,
            "published_date": "2026-09-01",
        },
        {
            "title": "PBOC cuts RRR",
            "url": "https://reuters.com/markets/asia/pboc",
            "content": "short",
            "score": 0.42,
        },
    ],
    "response_time": 1.2,
}


# ------------------------------ 纯函数 ------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", None),
        ("   ", None),
        # 旧配置填的是完整端点，SDK 需要的是 base_url
        ("https://api.tavily.com/search", "https://api.tavily.com"),
        ("https://api.tavily.com/search/", "https://api.tavily.com"),
        (" https://gw.local/tavily/ ", "https://gw.local/tavily"),
        ("https://gw.local/tavily", "https://gw.local/tavily"),
    ],
)
def test_api_base_url_normalization(raw, expected):
    assert ws._api_base_url(raw) == expected


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.cls.cn/detail/1", "cls.cn"),
        ("https://reuters.com/a", "reuters.com"),
        ("http://WWW.Sina.com.cn/x", "sina.com.cn"),
        ("", ""),
    ],
)
def test_publisher_from_url(url, expected):
    assert ws._publisher(url) == expected


def test_resolve_api_key_prefers_settings_then_env(monkeypatch):
    assert ws._resolve_api_key(_settings()) == "tvly-test"
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-env")
    assert ws._resolve_api_key(_settings(web_search_api_key="")) == "tvly-env"
    assert ws._resolve_api_key(_settings(web_search_api_key="tvly-cfg")) == "tvly-cfg"


# ------------------------------ 降级守卫 ------------------------------


async def test_disabled_raises_unavailable():
    with pytest.raises(ws.WebSearchUnavailable, match="WEB_SEARCH_ENABLED"):
        await ws.web_search("央行降准", settings=_settings(web_search_enabled=False))


async def test_missing_api_key_raises_unavailable():
    with pytest.raises(ws.WebSearchUnavailable, match="API Key"):
        await ws.web_search("央行降准", settings=_settings(web_search_api_key=""))


async def test_api_key_falls_back_to_env(monkeypatch):
    """配置里没填 Key 时，回退到 SDK 约定的 TAVILY_API_KEY。"""
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-env")
    stub = StubClient(SAMPLE_RESPONSE)
    out = await ws.web_search("央行降准", settings=_settings(web_search_api_key=""), client=stub)
    assert out and stub.params is not None


# ------------------------------ 参数装配 ------------------------------


async def test_default_params_come_from_settings():
    stub = StubClient(SAMPLE_RESPONSE)
    await ws.web_search("央行降准", settings=_settings(), client=stub)
    assert stub.params == {
        "query": "央行降准",
        "max_results": 5,
        "topic": "news",
        "search_depth": "basic",
        "time_range": "week",
        "days": None,
        "include_domains": [],
        "exclude_domains": [],
        "include_answer": False,
        "include_raw_content": False,
        "timeout": 30.0,
        "language": "zh-cn",
    }


async def test_explicit_args_override_settings():
    stub = StubClient(SAMPLE_RESPONSE)
    await ws.web_search(
        "美联储降息",
        max_results=8,
        settings=_settings(),
        topic="finance",
        search_depth="advanced",
        time_range="day",
        include_domains=["reuters.com"],
        exclude_domains=["spam.com"],
        include_answer="basic",
        client=stub,
    )
    assert stub.params["max_results"] == 8
    assert stub.params["topic"] == "finance"
    assert stub.params["search_depth"] == "advanced"
    assert stub.params["time_range"] == "day"
    assert stub.params["include_domains"] == ["reuters.com"]
    assert stub.params["exclude_domains"] == ["spam.com"]
    assert stub.params["include_answer"] == "basic"


async def test_days_disables_time_range():
    """days 与 time_range 互斥，给了 days 就不能再带 time_range。"""
    stub = StubClient(SAMPLE_RESPONSE)
    await ws.web_search("降准", settings=_settings(), days=3, client=stub)
    assert stub.params["days"] == 3
    assert stub.params["time_range"] is None


@pytest.mark.parametrize(
    "requested,expected",
    [
        (None, 5),  # 取配置默认
        (1, 1),
        (20, 20),  # Tavily 上限
        (99, 20),  # 超限截断
        (0, 1),  # 下限收敛
        (-3, 1),
    ],
)
async def test_max_results_is_clamped(requested, expected):
    stub = StubClient(SAMPLE_RESPONSE)
    await ws.web_search("降准", requested, settings=_settings(), client=stub)
    assert stub.params["max_results"] == expected


async def test_empty_time_range_and_language_are_dropped():
    stub = StubClient(SAMPLE_RESPONSE)
    await ws.web_search(
        "降准",
        settings=_settings(web_search_time_range="", web_search_language=""),
        client=stub,
    )
    assert stub.params["time_range"] is None
    assert "language" not in stub.params


async def test_domains_from_settings_use_list_semantics():
    stub = StubClient(SAMPLE_RESPONSE)
    await ws.web_search(
        "降准",
        settings=_settings(
            web_search_include_domains=["x.com", "y.com"],
            web_search_exclude_domains=["junk.com"],
        ),
        client=stub,
    )
    assert stub.params["include_domains"] == ["x.com", "y.com"]
    assert stub.params["exclude_domains"] == ["junk.com"]


# ------------------------------ 结果归一化 ------------------------------


async def test_results_are_normalized():
    stub = StubClient(SAMPLE_RESPONSE)
    out = await ws.web_search("央行降准", settings=_settings(), client=stub)
    assert [o["title"] for o in out] == ["华泰 | 固收：政策成为首要关注点", "PBOC cuts RRR"]
    first, second = out
    assert first["url"] == "https://www.cls.cn/detail/123"
    assert first["publisher"] == "cls.cn"
    assert first["published_at"] == "2026-09-01"
    assert first["score"] == pytest.approx(0.91)
    # content 超长要截断到 SNIPPET_LIMIT
    assert len(first["snippet"]) == ws.SNIPPET_LIMIT
    # 没有 published_date 的来源留空，不能塞 None
    assert second["published_at"] == ""
    assert second["publisher"] == "reuters.com"


async def test_results_are_truncated_to_max_results():
    stub = StubClient(SAMPLE_RESPONSE)
    out = await ws.web_search("降准", 1, settings=_settings(), client=stub)
    assert len(out) == 1


async def test_empty_results_returns_empty_list():
    out = await ws.web_search("降准", settings=_settings(), client=StubClient({"results": []}))
    assert out == []
    # 响应里没有 results 键也不能炸
    out = await ws.web_search("降准", settings=_settings(), client=StubClient({}))
    assert out == []


async def test_missing_fields_do_not_raise():
    stub = StubClient({"results": [{"url": "https://a.com"}, {}]})
    out = await ws.web_search("降准", settings=_settings(), client=stub)
    assert out[0] == {
        "title": "",
        "url": "https://a.com",
        "publisher": "a.com",
        "published_at": "",
        "snippet": "",
        "score": 0.0,
    }
    assert out[1]["url"] == "" and out[1]["publisher"] == ""


# ------------------------------ 异常处理 ------------------------------


@pytest.mark.parametrize(
    "error,keyword",
    [
        (MissingAPIKeyError(), "API Key 无效"),
        (InvalidAPIKeyError("bad key"), "API Key 无效"),
        (UsageLimitExceededError("quota"), "配额"),
        (ForbiddenError("forbidden"), "请求被拒绝"),
        (BadRequestError("query too short"), "请求被拒绝"),
        (TavilyTimeoutError(30), "超时"),
        (RuntimeError("boom"), "调用失败"),
    ],
)
async def test_sdk_errors_are_wrapped(error, keyword):
    """SDK 的异常一律收敛成 WebSearchError，且保留原因便于排查。"""
    with pytest.raises(ws.WebSearchError) as excinfo:
        await ws.web_search("降准", settings=_settings(), client=StubClient(error=error))
    assert keyword in str(excinfo.value)


async def test_error_is_not_reported_as_unavailable():
    """调用失败 ≠ 能力缺失，不能让 Agent 误判成「没配置」而改写降级路径。"""
    with pytest.raises(ws.WebSearchError):
        await ws.web_search("降准", settings=_settings(), client=StubClient(error=RuntimeError("x")))
    with pytest.raises(ws.WebSearchUnavailable):
        await ws.web_search("降准", settings=_settings(web_search_enabled=False))


# ------------------------------ 客户端生命周期 ------------------------------


async def test_injected_client_is_not_closed():
    stub = StubClient(SAMPLE_RESPONSE)
    await ws.web_search("降准", settings=_settings(), client=stub)
    assert stub.closed is False


async def test_owned_client_is_closed_even_on_error(monkeypatch):
    """自建的 client 必须关闭，否则每次检索都会漏一个连接池。"""
    stub = StubClient(error=RuntimeError("boom"))
    _install_client_factory(monkeypatch, stub)
    with pytest.raises(ws.WebSearchError):
        await ws.web_search("降准", settings=_settings())
    assert stub.closed is True


async def test_client_built_with_key_and_base_url(monkeypatch):
    stub = StubClient(SAMPLE_RESPONSE)
    init_kwargs = _install_client_factory(monkeypatch, stub)
    await ws.web_search(
        "降准",
        settings=_settings(
            web_search_api_key="tvly-cfg",
            web_search_base_url="https://api.tavily.com/search",
        ),
    )
    assert init_kwargs["api_key"] == "tvly-cfg"
    # base_url 必须归一成不带 /search 的形式，否则会打到 <base>/search/search
    assert init_kwargs["api_base_url"] == "https://api.tavily.com"


def _install_client_factory(monkeypatch, stub: StubClient) -> dict:
    """把模块里的 AsyncTavilyClient 换成返回桩实例的工厂，返回构造入参。"""
    init_kwargs: dict = {}

    class Factory:
        def __init__(self, **kwargs):
            init_kwargs.update(kwargs)

        async def search(self, **params):
            return await stub.search(**params)

        async def close(self):
            await stub.close()

    monkeypatch.setattr(ws, "AsyncTavilyClient", Factory)
    return init_kwargs


# ------------------------------ format_results ------------------------------


def test_format_results_empty():
    assert ws.format_results([]) == "（外部检索不可用或未配置）"


def test_format_results_includes_publisher_and_date():
    text = ws.format_results(
        [
            {
                "title": "央行降准",
                "publisher": "cls.cn",
                "published_at": "2026-09-01",
                "snippet": "正文",
                "url": "https://www.cls.cn/detail/1",
            }
        ]
    )
    assert "[1] 央行降准 (cls.cn · 2026-09-01)" in text
    assert "正文" in text
    assert "https://www.cls.cn/detail/1" in text


def test_format_results_without_optional_fields():
    text = ws.format_results([{"title": "T", "url": "https://a.com"}])
    assert "[1] T (来源未知)" in text
    assert "https://a.com" in text


def test_format_results_numbers_all_hits():
    items = [{"title": f"T{i}", "url": f"https://a.com/{i}"} for i in range(3)]
    text = ws.format_results(items)
    assert "[1]" in text and "[2]" in text and "[3]" in text


# ------------------------------ LangChain 工具包装 ------------------------------


async def test_langchain_tool_degrades_when_disabled(monkeypatch):
    from fin_news.agents.tools.langchain_tools import web_search as lc_tool

    # 包装层走 get_settings() 全局单例，必须显式钉死，否则 .env 里开着搜索就会真发请求
    monkeypatch.setattr(ws, "get_settings", lambda: _settings(web_search_enabled=False))
    out = await lc_tool.ainvoke({"query": "降准", "max_results": 3})
    assert out.startswith("（外部检索不可用：")


async def test_langchain_tool_returns_formatted_results(monkeypatch):
    from fin_news.agents.tools.langchain_tools import web_search as lc_tool

    async def _fake(query, max_results=5, settings=None, **kwargs):
        return [{"title": "央行降准", "publisher": "cls.cn", "snippet": "正文", "url": "https://a.com"}]

    # 包装函数内部是延迟 import，替换模块属性即可
    monkeypatch.setattr(ws, "web_search", _fake)
    out = await lc_tool.ainvoke({"query": "降准", "max_results": 1})
    assert "[1] 央行降准 (cls.cn)" in out
    assert "https://a.com" in out


async def test_langchain_tool_reports_call_failure(monkeypatch):
    from fin_news.agents.tools.langchain_tools import web_search as lc_tool

    async def _fake(query, max_results=5, settings=None, **kwargs):
        raise ws.WebSearchError("配额炸了")

    monkeypatch.setattr(ws, "web_search", _fake)
    out = await lc_tool.ainvoke({"query": "降准"})
    assert "外部检索失败" in out
    assert "配额炸了" in out


# ------------------------------ 真实调用（默认跳过） ------------------------------
#
# 桩测试只能证明「我们按 SDK 的用法在调」，证明不了「SDK 真能连通、字段真长这样」。
# 下面这组用例打真实 API，用于验证参数透传与响应字段的实际形态，会消耗 Tavily 额度。
# 在导入期就取好凭据：autouse 的 _clean_env 会在每个用例前清掉环境变量。

_LIVE_ENABLED = os.getenv("FIN_NEWS_LIVE_TESTS") == "1"
_LIVE_KEY = os.getenv("TAVILY_API_KEY") or os.getenv("WEB_SEARCH_API_KEY") or ""
_LIVE_BASE_URL = os.getenv("WEB_SEARCH_BASE_URL", "")
_LIVE_READY = _LIVE_ENABLED and bool(_LIVE_KEY)

requires_live = pytest.mark.skipif(
    not _LIVE_READY,
    reason="需要 FIN_NEWS_LIVE_TESTS=1 且配置 TAVILY_API_KEY（会消耗额度，故默认跳过）",
)


def _live_settings(**kw) -> Settings:
    """真实调用的配置：Key 直接塞进对象，不依赖用例运行时的环境变量。"""
    base = dict(
        web_search_enabled=True,
        web_search_api_key=_LIVE_KEY,
        web_search_base_url=_LIVE_BASE_URL,
    )
    base.update(kw)
    return Settings(_env_file=None, **base)


@pytest.mark.live
@requires_live
async def test_live_search_returns_normalized_results():
    """真实检索：结果结构与归一化字段必须和桩测试的假设一致。"""
    out = await ws.web_search("央行降准 市场预期", max_results=3, settings=_live_settings())
    assert out, "真实检索应至少返回一条结果"
    assert len(out) <= 3
    for item in out:
        assert set(item) == {"title", "url", "publisher", "published_at", "snippet", "score"}
        assert item["url"].startswith(("http://", "https://"))
        assert item["publisher"], f"publisher 不应为空：{item['url']}"
        assert isinstance(item["title"], str)
        assert isinstance(item["snippet"], str)
        assert len(item["snippet"]) <= ws.SNIPPET_LIMIT
        assert isinstance(item["score"], float)
    # 归一化后的结果要能直接喂给 Agent
    text = ws.format_results(out)
    assert "[1]" in text and "https://" in text


@pytest.mark.live
@requires_live
async def test_live_topic_news_carries_published_date():
    """默认 topic=news 的理由：只有 news 才返回 published_date（供 Agent 标注信息时点）。"""
    out = await ws.web_search("央行 货币政策", max_results=5, settings=_live_settings())
    assert out
    assert any(item["published_at"] for item in out), "topic=news 应带回发布时间"


@pytest.mark.live
@requires_live
async def test_live_include_domains_is_enforced_by_api():
    """域名白名单是透传给 API 的，这里验证真实生效（不只是参数拼对了）。"""
    out = await ws.web_search(
        "quantitative easing",
        max_results=3,
        settings=_live_settings(web_search_time_range="", web_search_language=""),
        include_domains=["wikipedia.org"],
    )
    assert out, "限定了域名仍应命中结果，否则用例失去意义"
    for item in out:
        assert item["publisher"].endswith("wikipedia.org"), f"越界来源：{item['url']}"


@pytest.mark.live
@requires_live
async def test_live_invalid_key_is_reported_as_call_failure():
    """真实 API 的 401 要映射成 WebSearchError，不能被当成「未配置」而走错降级分支。"""
    with pytest.raises(ws.WebSearchError, match="API Key 无效"):
        await ws.web_search("央行降准", settings=_live_settings(web_search_api_key="tvly-not-a-real-key"))
