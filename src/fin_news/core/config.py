"""应用配置（pydantic-settings，从 .env / 环境变量读取）。"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ProviderName = Literal["volcengine", "deepseek"]
LLMRole = Literal["scoring", "analysis", "qa", "embedding"]


@dataclass(frozen=True)
class ProviderConfig:
    """一个 OpenAI 兼容的模型供应商。"""

    name: str
    base_url: str
    api_key: str


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------- 应用 ----------------
    app_name: str = "fin-news-v5"
    env: str = "dev"
    debug: bool = True
    log_level: str = "INFO"

    # ---------------- 数据库 ----------------
    postgres_host: str = "localhost"
    postgres_port: int = 5433
    postgres_user: str = "finnews"
    postgres_password: str = "finnews"
    postgres_db: str = "finnews"
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_echo: bool = False

    @property
    def async_database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def sync_database_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    # ---------------- Tushare ----------------
    tushare_token: str = ""
    tushare_timeout: int = 30
    tushare_qps: float = 3.0
    news_sources: list[str] = Field(default_factory=lambda: ["cls", "wallstreetcn"])

    # ---------------- 接入调度 ----------------
    ingest_interval_seconds: int = 60
    ingest_overlap_seconds: int = 300
    ingest_first_lookback_hours: int = 6

    # ---------------- 评分 ----------------
    scoring_batch_size: int = 30
    scoring_window_seconds: int = 15
    scoring_max_content_chars: int = 800
    scoring_concurrency: int = 4
    # score > 该阈值才做向量化与深度分析（(0,3] 为噪声）
    score_threshold_vectorize: int = 3

    # ---------------- Embedding ----------------
    embedding_provider: ProviderName = "volcengine"
    embedding_dim: int = 1024
    embedding_batch_size: int = 32

    # ---------------- LLM ----------------
    llm_default_provider: ProviderName = "volcengine"
    llm_fallback_provider: ProviderName = "deepseek"
    llm_timeout_seconds: int = 120
    llm_max_retries: int = 2

    volcengine_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    volcengine_api_key: str = ""
    volcengine_model_scoring: str = "doubao-lite-32k"
    volcengine_model_analysis: str = "doubao-pro-32k"
    volcengine_model_qa: str = "doubao-pro-32k"
    volcengine_model_embedding: str = "doubao-embedding"

    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_api_key: str = ""
    deepseek_model_scoring: str = "deepseek-chat"
    deepseek_model_analysis: str = "deepseek-chat"
    deepseek_model_qa: str = "deepseek-chat"

    # ---------------- Agent ----------------
    # 关闭后，深度分析 Agent 退化为「预取工具结果 + 单次结构化调用」
    use_deep_agents: bool = True
    analysis_concurrency: int = 4
    analysis_timeout_seconds: int = 300

    web_search_enabled: bool = False
    web_search_base_url: str = ""
    web_search_api_key: str = ""

    # ---------------- Pipeline ----------------
    worker_poll_interval_seconds: float = 2.0
    worker_batch_limit: int = 50
    event_max_attempts: int = 5
    event_backoff_base_seconds: int = 30
    event_retention_days: int = 7

    # ---------------- 盘前 / 盘后 ----------------
    pre_market_hour: int = 7
    pre_market_minute: int = 30
    post_market_hour: int = 15
    post_market_minute: int = 30

    # ---------------- 追问 ----------------
    chat_top_k: int = 8
    chat_recent_days: int = 7

    # ---------------- API ----------------
    api_prefix: str = "/api/v1"
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])

    # ---------------- 派生方法 ----------------
    def provider(self, name: ProviderName) -> ProviderConfig:
        if name == "volcengine":
            return ProviderConfig(name, self.volcengine_base_url, self.volcengine_api_key)
        if name == "deepseek":
            return ProviderConfig(name, self.deepseek_base_url, self.deepseek_api_key)
        raise ValueError(f"未知 provider: {name}")

    def model_for(self, provider: ProviderName, role: LLMRole) -> str:
        if provider == "volcengine":
            return {
                "scoring": self.volcengine_model_scoring,
                "analysis": self.volcengine_model_analysis,
                "qa": self.volcengine_model_qa,
                "embedding": self.volcengine_model_embedding,
            }[role]
        if provider == "deepseek":
            # DeepSeek 侧不提供 embedding，回落到默认 provider
            return {
                "scoring": self.deepseek_model_scoring,
                "analysis": self.deepseek_model_analysis,
                "qa": self.deepseek_model_qa,
                "embedding": self.volcengine_model_embedding,
            }[role]
        raise ValueError(f"未知 provider: {provider}")

    def has_llm_credentials(self) -> bool:
        """是否存在任一可用的模型凭据（决定是否启用分析链路）。"""
        return bool(self.volcengine_api_key or self.deepseek_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
