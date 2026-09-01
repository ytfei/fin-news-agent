"""Embedding 客户端：统一走 LangChain Embeddings 接口（火山 / DeepSeek 兼容）。

保留自建 Embedder 的原因是业务侧需要：
* 按 embedding_batch_size 分批（长资讯分块后可能几十块）
* 写入前的维度校验（维度不一致必须终止，否则污染向量索引）
"""
from __future__ import annotations

from fin_news.agents.llm.factory import get_model_factory
from fin_news.core.config import Settings, get_settings
from fin_news.core.logging import get_logger

logger = get_logger("agents.embedding")


class DimensionMismatch(ValueError):
    """向量维度与配置不一致（禁止写入，避免污染索引）。"""


class Embedder:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._embeddings = None

    @property
    def embeddings(self):
        """LangChain Embeddings 客户端（懒加载）。

        火山模型名不在 tiktoken 词表，工厂里已关闭 check_embedding_ctx_length。
        """
        if self._embeddings is None:
            self._embeddings = get_model_factory(self.settings).embeddings()
        return self._embeddings

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self.settings.model_for(self.settings.embedding_provider, "embedding")  # type: ignore[arg-type]
        vectors: list[list[float]] = []
        batch_size = max(1, self.settings.embedding_batch_size)

        for i in range(0, len(texts), batch_size):
            batch = [t.replace("\n", " ").strip() or " " for t in texts[i : i + batch_size]]
            vectors.extend(await self.embeddings.aembed_documents(batch))

        self._validate(vectors)
        logger.debug("embedding 完成", model=model, texts=len(texts), dim=len(vectors[0]) if vectors else 0)
        return vectors

    async def embed_one(self, text: str) -> list[float]:
        vecs = await self.embed([text])
        return vecs[0] if vecs else []

    def _validate(self, vectors: list[list[float]]) -> None:
        expected = self.settings.embedding_dim
        for vec in vectors:
            if len(vec) != expected:
                raise DimensionMismatch(
                    f"embedding 维度不匹配：期望 {expected}，实际 {len(vec)}。"
                    "请检查 EMBEDDING_DIM 与所选模型是否一致。"
                )


_embedder: Embedder | None = None


def get_embedder(settings: Settings | None = None) -> Embedder:
    global _embedder
    if _embedder is None:
        _embedder = Embedder(settings)
    return _embedder
