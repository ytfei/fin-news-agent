"""配置解析：列表型字段必须容忍 shell source .env 造成的各种形态。"""
import importlib
import os

import pytest

from fin_news.core.config import Settings, parse_str_list


@pytest.mark.parametrize(
    "raw,expected",
    [
        (["cls", "wallstreetcn"], ["cls", "wallstreetcn"]),  # 已经是列表
        ('["cls","wallstreetcn"]', ["cls", "wallstreetcn"]),  # 标准 JSON
        ("[cls,wallstreetcn]", ["cls", "wallstreetcn"]),  # 引号被 shell 吃掉
        ('["cls", "wallstreetcn"]', ["cls", "wallstreetcn"]),  # 带空格
        ("cls,wallstreetcn", ["cls", "wallstreetcn"]),  # 纯逗号分隔
        ("cls, wallstreetcn ,", ["cls", "wallstreetcn"]),  # 首尾空格与空项
        ("*", ["*"]),
        ("[*]", ["*"]),
        ("", []),
        (None, []),
    ],
)
def test_parse_str_list(raw, expected):
    assert parse_str_list(raw) == expected


def test_settings_accepts_shell_mangled_env(monkeypatch):
    """复现线上问题：zsh dotenv 插件把 NEWS_SOURCES 变成 [cls,wallstreetcn]。"""
    monkeypatch.setenv("NEWS_SOURCES", "[cls,wallstreetcn]")
    monkeypatch.setenv("CORS_ORIGINS", "[*]")
    s = Settings(_env_file=None)
    assert s.news_sources == ["cls", "wallstreetcn"]
    assert s.cors_origins == ["*"]


def test_settings_defaults(monkeypatch):
    monkeypatch.delenv("NEWS_SOURCES", raising=False)
    s = Settings(_env_file=None)
    assert s.news_sources == ["cls", "wallstreetcn", "yicai"]
    assert s.score_threshold_vectorize == 3


def test_settings_reads_env_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        'NEWS_SOURCES=["cls","wallstreetcn"]\nCORS_ORIGINS=*\nPOSTGRES_PORT=6000\n',
        encoding="utf-8",
    )
    # 清掉可能污染进程环境的相关变量，确保读到的是文件内容
    for key in ("NEWS_SOURCES", "CORS_ORIGINS", "POSTGRES_PORT"):
        monkeypatch.delenv(key, raising=False)
    s = Settings(_env_file=env_file)
    assert s.news_sources == ["cls", "wallstreetcn"]
    assert s.cors_origins == ["*"]
    assert s.postgres_port == 6000


def test_module_singleton_importable():
    """配置模块在任意环境下都必须能被导入（alembic / cli / api 都依赖它）。"""
    module = importlib.import_module("fin_news.core.config")
    assert isinstance(module.settings, Settings)


def test_has_llm_credentials(monkeypatch):
    # 进程环境里可能已经有真实 Key，先清掉再断言
    for key in ("VOLCENGINE_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    assert Settings(_env_file=None).has_llm_credentials() is False
    assert Settings(_env_file=None, volcengine_api_key="x").has_llm_credentials() is True
    assert Settings(_env_file=None, deepseek_api_key="y").has_llm_credentials() is True


def test_unrelated_env_does_not_break(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-xxx")
    monkeypatch.setenv("LANGSMITH_PROJECT", "StockAnalyzer")
    s = Settings(_env_file=None)
    assert s.app_name == "fin-news-v5"


def test_env_prefix_is_not_used():
    """确认没有配置 env_prefix，否则 .env 里的裸变量名会读不到。"""
    assert os.environ.get("FIN_NEWS_APP_NAME") is None
    s = Settings(_env_file=None)
    assert s.env in {"dev", "test", "prod", "staging", "local"}


def test_score_and_embed_concurrency_defaults(monkeypatch):
    """小批并发新增配置必须提供合理的进程内默认值。"""
    for key in ("SCORING_SUB_BATCH_SIZE", "EMBEDDING_CONCURRENCY"):
        monkeypatch.delenv(key, raising=False)
    s = Settings(_env_file=None)
    assert s.scoring_sub_batch_size == 10
    assert s.embedding_concurrency == 16
    assert s.embedding_batch_size == 32  # 已弃用但保留兼容 .env
