"""Embedding 客户端（OpenAI 兼容 /embeddings 接口）。"""
from __future__ import annotations

from openai import AsyncOpenAI

from fin_news.core.config import Settings, get_settings
from fin_news.core.logging import get_logger

logger = get_logger("agents.embedding")


class DimensionMismatch(ValueError):
    """向量维度与配置不一致（禁止写入，避免污染索引）。"""


class Embedder:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._client: AsyncOpenAI | None = None

    @property
    def client(self) -> AsyncOpenAI:
        if self._client is None:
            cfg = self.settings.provider(self.settings.embedding_provider)  # type: ignore[arg-type]
            if not cfg.api_key:
                raise RuntimeError(
                    f"embedding provider {self.settings.embedding_provider} 未配置 api_key"
                )
            self._client = AsyncOpenAI(base_url=cfg.base_url, api_key=cfg.api_key, timeout=60)
        return self._client

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self.settings.model_for(self.settings.embedding_provider, "embedding")  # type: ignore[arg-type]
        vectors: list[list[float]] = []
        batch_size = max(1, self.settings.embedding_batch_size)

        for i in range(0, len(texts), batch_size):
            batch = [t.replace("\n", " ").strip() or " " for t in texts[i : i + batch_size]]
            resp = await self.client.embeddings.create(model=model, input=batch)
            for item in resp.data:
                vectors.append(list(item.embedding))

        self._validate(vectors)
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
