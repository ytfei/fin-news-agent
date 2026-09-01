"""LangChain 模型工厂：按角色装配 ChatModel / Embeddings。

设计要点：
* 统一走 OpenAI 兼容协议，火山引擎与 DeepSeek 只差 base_url / api_key / model
* 主备降级用 LangChain 原生 `with_fallbacks()`，流式场景同样生效
* 客户端本身是轻量对象，按 (provider, role) 缓存复用
"""
from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI

from fin_news.agents.llm.client import LLMUnavailable
from fin_news.core.config import LLMRole, ProviderName, Settings, get_settings
from fin_news.core.logging import get_logger

logger = get_logger("agents.llm.factory")

# 各角色的默认温度：评分要稳定，分析/追问要一点发散
_TEMPERATURE: dict[str, float] = {
    "scoring": 0.0,
    "analysis": 0.2,
    "qa": 0.3,
}


class ModelFactory:
    """按角色构建带降级的 ChatModel。

    注意：Embedding 已独立到 `agents/embeddings.py` 的 `Embedder`（直连火山方舟
    `/embeddings/multimodal` 接口），不再走 OpenAIEmbeddings —— doubao-embedding-vision
    的接口与 input 格式与 OpenAI 的 /embeddings 不兼容。
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._cache: dict[tuple[str, str], ChatOpenAI] = {}

    # ------------------------------------------------------------------
    def build(self, provider: ProviderName, role: LLMRole) -> ChatOpenAI | None:
        """构建单个 provider 的客户端；未配置 api_key 时返回 None。"""
        cfg = self.settings.provider(provider)
        if not cfg.api_key:
            return None

        key = (provider, role)
        if key not in self._cache:
            self._cache[key] = ChatOpenAI(
                base_url=cfg.base_url,
                api_key=cfg.api_key,
                model=self.settings.model_for(provider, role),
                temperature=_TEMPERATURE.get(role, 0.2),
                timeout=self.settings.llm_timeout_seconds,
                max_retries=self.settings.llm_max_retries,
            )
        return self._cache[key]

    def chat(
        self, role: LLMRole, *, with_fallback: bool = True, temperature: float | None = None
    ) -> BaseChatModel:
        """返回该角色可用的 ChatModel（主模型失败自动切备）。"""
        primary = self.build(self.settings.llm_default_provider, role)
        fallback_provider = self.settings.llm_fallback_provider
        fallback = (
            self.build(fallback_provider, role)
            if with_fallback and fallback_provider != self.settings.llm_default_provider
            else None
        )

        model: BaseChatModel | None = primary
        if primary is not None and fallback is not None:
            model = primary.with_fallbacks([fallback])
        elif primary is None:
            model = fallback

        if model is None:
            raise LLMUnavailable(
                f"角色 {role} 没有可用 provider："
                f"{self.settings.llm_default_provider} / {fallback_provider} 均未配置 api_key"
            )

        if temperature is not None:
            model = model.bind(temperature=temperature)
        return model

    # ------------------------------------------------------------------
    def structured(
        self,
        role: LLMRole,
        schema: type,
        *,
        method: str | None = None,
        include_raw: bool = False,
    ) -> Any:
        """带结构化输出的 Runnable。

        include_raw=True 时返回 {"raw": AIMessage, "parsed": schema, "parsing_error": ...}，
        便于取 token 用量与解析错误（评分图依赖它做用量统计与降级判断）。
        """
        model = self.chat(role)
        methods = [method] if method else ["json_schema", "function_calling"]
        last_error: Exception | None = None
        for m in methods:
            try:
                return model.with_structured_output(
                    schema,  # type: ignore[arg-type]
                    method=m,  # type: ignore[arg-type]
                    include_raw=include_raw,
                )
            except Exception as exc:  # noqa: BLE001 - 不同模型的支持度不同
                last_error = exc
                logger.warning("结构化输出方式不可用，尝试下一种", role=role, method=m, error=str(exc)[:200])
        raise LLMUnavailable(f"角色 {role} 不支持结构化输出：{last_error}")


_factory: ModelFactory | None = None


def get_model_factory(settings: Settings | None = None) -> ModelFactory:
    global _factory
    if _factory is None:
        _factory = ModelFactory(settings)
    return _factory
