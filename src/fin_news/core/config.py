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


def parse_json_dict(value: object) -> dict[str, dict[str, float]]:
    """把 ".env / 环境变量" 里的 JSON 对象解析成 dict。

    与 parse_str_list 同源的容错：shell source .env 时会吃掉引号，
    解析失败时静默返回空 dict（回落到代码内默认单价表），不阻断启动。
    """
    if value is None:
        return {}
    if isinstance(value, dict):
        return {str(k): v for k, v in value.items() if isinstance(v, dict)}
    text = str(value).strip()
    if not text or text in ("{}", "[]", "null", "None"):
        return {}
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return {}
    if isinstance(parsed, dict):
        return {str(k): v for k, v in parsed.items() if isinstance(v, dict)}
    return {}


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
    scoring_concurrency: int = 10
    # 评分子批条数：整批资讯按此切片并发评分（小批输出更快、漏评可局部补打）
    scoring_sub_batch_size: int = 5
    # score > 该阈值才做向量化与深度分析（(0,3] 为噪声）
    score_threshold_vectorize: int = 3

    # ---------------- Embedding ----------------
    # doubao-embedding-vision 多模态向量化：维度由请求参数 dimensions 指定，
    # 可选 1024 / 2048（旧文本模型 doubao-embedding-text-240715 固定 2560 维，已弃用）
    embedding_provider: ProviderName = "volcengine"
    embedding_dim: int = 2048
    # 已弃用：旧版以该值分批并发 embedding；现由 embedding_concurrency 统一限流，保留以兼容 .env
    embedding_batch_size: int = 32
    # embedding HTTP 请求的进程级并发上限（火山 multimodal 单样本语义只能逐条请求，靠并发提吞吐）
    embedding_concurrency: int = 16
    # 429 / 5xx 的退避重试次数（多模态接口限流时避免整批失败）
    embedding_max_retries: int = 3
    # 进程级 QPS 上限（令牌桶）：并发闸门只能控「同时 in-flight 数」，控不住每秒请求数。
    # 0 表示不启用，仅用 embedding_concurrency 控并发。
    embedding_qps: float = 0

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

    # ---------------- 成本 ----------------
    # 模型单价（元 / 百万 token，区分 input / output），按 model 名覆盖
    # agents/llm/pricing.py 里的默认表。
    #
    # ⚠️ 默认表是**占位量级**，请按火山方舟 / DeepSeek 控制台的实际单价校准；
    # 模型会调价，硬编码必然过期（OpenTelemetry GenAI 规范的明确警告）。
    # 校准后可执行 `fin_news.cli cost-recalc` 按新单价重算历史成本。
    #
    # 例：MODEL_PRICING={"doubao-pro-32k":{"input":0.8,"output":2.0}}
    model_pricing: Annotated[dict[str, dict[str, float]], NoDecode] = Field(
        default_factory=dict
    )

    # ---------------- Agent ----------------
    # 关闭后，深度分析 Agent 退化为「预取工具结果 + 单次结构化调用」
    use_deep_agents: bool = True
    analysis_concurrency: int = 4
    analysis_timeout_seconds: int = 300
    # 跳过「已有当前版本有效报告」的资讯，避免重复分析烧钱。
    # 关掉可强制重跑（如改了 prompt 后想让全部资讯重新分析）。
    analysis_skip_existing: bool = True
    # 盘前/盘后简报：走 ReAct 深度分析（多轮工具调用 + 子 agent 并行），
    # 耗时预算比逐条资讯分析更宽松
    brief_timeout_seconds: int = 1800
    # ReAct 循环步数上限（LangGraph 按节点计数，子 agent 内部也计步）。
    # LangGraph 默认 10007 等于没有上限、纯靠超时兜底，深度场景需显式收紧。
    agent_recursion_limit: int = 200
    # 步骤级追踪：把 ReAct 每一步（工具调用 / 子 agent / LLM 往返）的名称、
    # 入参摘要与耗时打到日志。日志随执行实时输出，因此超时时也能看到最后停在哪一步。
    # 默认关闭：深度场景单次运行可达数十步，全程开启会显著刷屏。
    agent_trace_enabled: bool = True
    # 单条追踪日志里「入参 / 结果」摘要的最大字符数（防止长文刷屏）
    agent_trace_max_chars: int = 300

    # 评分 / 分析的实现层：langgraph 走 LangGraph 图 + 原生结构化输出，
    # legacy 走自研 llm/client.py；langgraph 失败会自动回退 legacy
    agent_framework: AgentFramework = "langgraph"
    # 双跑对比：两套实现各跑一次，只记录差异，不影响入库结果（临时灰度用）
    score_dual_run: bool = False
    # 退化护栏：批内分数种类过少（模型"偷懒"给同一个分）时重试一次，取更好的结果
    score_retry_on_degenerate: bool = False
    # LangGraph checkpointer 的独立 schema，避免污染业务表与 Alembic autogenerate
    langgraph_schema: str = "langgraph"
    # LangSmith 项目名（追踪用，未配置 API Key 时自动不生效）
    langchain_project: str = "fin-news-v5"

    # ---------------- Skills / 微信公众号 ----------------
    # 写文章 Agent 的 skills 目录（提示词型 SKILL.md + 工具型 tool.py），
    # 相对路径按进程工作目录解析；CLI `article write --skills-dir` 可覆盖。
    skills_dir: str = "skills"

    # ---------------- 外部检索（Tavily） ----------------
    # 见 https://docs.tavily.com/sdk/python/reference
    web_search_enabled: bool = False
    # 留空时回退到 SDK 约定的 TAVILY_API_KEY 环境变量
    web_search_api_key: str = ""
    # 留空走 SDK 默认 https://api.tavily.com；自建网关 / 代理时填网关地址
    web_search_base_url: str = ""
    web_search_topic: Literal["general", "news", "finance"] = "news"
    web_search_depth: Literal["basic", "advanced", "fast", "ultra-fast"] = "basic"
    # day / week / month / year，留空表示不限时间
    web_search_time_range: Literal["", "day", "week", "month", "year"] = "week"
    web_search_max_results: int = 5
    # ISO 639-1 或英文名，仅用于排序加权；留空表示不加权
    web_search_language: str = "zh-cn"
    web_search_timeout_seconds: float = 30.0
    web_search_include_domains: Annotated[list[str], NoDecode] = Field(default_factory=list)
    web_search_exclude_domains: Annotated[list[str], NoDecode] = Field(default_factory=list)

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

    @field_validator(
        "news_sources",
        "cors_origins",
        "web_search_include_domains",
        "web_search_exclude_domains",
        mode="before",
    )
    @classmethod
    def _coerce_str_list(cls, value: object) -> list[str]:
        return parse_str_list(value)

    @field_validator("model_pricing", mode="before")
    @classmethod
    def _coerce_json_dict(cls, value: object) -> dict[str, dict[str, float]]:
        return parse_json_dict(value)

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
