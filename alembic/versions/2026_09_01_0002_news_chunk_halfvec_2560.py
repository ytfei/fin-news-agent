"""news_chunk.embedding 改为 halfvec(2560)

原因：火山方舟可用的 embedding 模型（doubao-embedding-text-240715）固定输出 2560 维
且不支持 dimensions 降维，而 pgvector 的 HNSW / IVFFlat 索引对 float vector 的
上限是 2000 维。halfvec 支持到 4000 维且存储减半，因此列类型改为 halfvec。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TARGET_DIM = 2560
LEGACY_DIM = 1024


def upgrade() -> None:
    op.drop_index("idx_chunk_embedding", table_name="news_chunk")
    op.execute(f"ALTER TABLE news_chunk ALTER COLUMN embedding TYPE halfvec({TARGET_DIM})")
    op.create_index(
        "idx_chunk_embedding",
        "news_chunk",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_with={"m": "16", "ef_construction": "64"},
        postgresql_ops={"embedding": "halfvec_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_index("idx_chunk_embedding", table_name="news_chunk")
    op.execute(f"ALTER TABLE news_chunk ALTER COLUMN embedding TYPE vector({LEGACY_DIM})")
    op.create_index(
        "idx_chunk_embedding",
        "news_chunk",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_with={"m": "16", "ef_construction": "64"},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


# 保留 sa 引用：便于后续手工扩展本迁移（如分表回填）
_ = sa
