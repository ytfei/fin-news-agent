"""应用配置（pydantic-settings，从 .env / 环境变量读取）。"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

ProviderName = Literal["volcengine", "deepseek"]
LLMRole = Literal["scoring", "analysis", "qa", "embedding"]
# legacy = 自研 llm/client.py 调用层；langgraph = LangGraph / DeepAgents
AgentFramework = Literal["legacy", "langgraph"]


def parse_str_list(value: object) -> list[str]:
    """把 ".env / 环境变量" 里的列表值解析成 list[str]。

    同时兼容以下写法（shell 插件 source .env 时会吃掉引号，必须容错）：

        NEWS_SOURCES=["cls","wallstreetcn"]   # JSON
        NEWS_SOURCES=[cls,wallstreetcn]       # 引号被 shell 吃掉后的形态
        NEWS_SOURCES=cls,wallstreetcn         # 逗号分隔
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]

    text = str(value).strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            parsed = None
        if isinstance(parsed, list):
            return [str(x).strip() for x in parsed if str(x).strip()]
        # JSON 解析失败（引号被 shell 吃掉）时退化为去掉方括号再切分
        text = text.strip("[]")
    return [part.strip().strip("'\"") for part in text.split(",") if part.strip()]


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
    news_sources: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["cls", "wallstreetcn", "yicai"]
    )

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
    # doubao-embedding-vision 多模态向量化：维度由请求参数 dimensions 指定，
    # 可选 1024 / 2048（旧文本模型 doubao-embedding-text-240715 固定 2560 维，已弃用）
    embedding_provider: ProviderName = "volcengine"
    embedding_dim: int = 2048
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
    volcengine_model_embedding: str = "doubao-embedding-vision"

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

    # 评分 / 分析的实现层：langgraph 走 LangGraph 图 + 原生结构化输出，
    # legacy 走自研 llm/client.py；langgraph 失败会自动回退 legacy
    agent_framework: AgentFramework = "langgraph"
    # 双跑对比：两套实现各跑一次，只记录差异，不影响入库结果（临时灰度用）
    score_dual_run: bool = False
    # 退化护栏：批内分数种类过少（模型"偷懒"给同一个分）时重试一次，取更好的结果
    score_retry_on_degenerate: bool = True
    # LangGraph checkpointer 的独立 schema，避免污染业务表与 Alembic autogenerate
    langgraph_schema: str = "langgraph"
    # LangSmith 项目名（追踪用，未配置 API Key 时自动不生效）
    langchain_project: str = "fin-news-v5"

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
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["*"])

    @field_validator("news_sources", "cors_origins", mode="before")
    @classmethod
    def _coerce_str_list(cls, value: object) -> list[str]:
        return parse_str_list(value)

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
