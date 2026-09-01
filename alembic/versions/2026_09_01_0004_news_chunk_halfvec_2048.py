"""news_chunk.embedding 改为 halfvec(2048)

原因：embedding 模型从 doubao-embedding-text-240715（固定 2560 维）切换为
doubao-embedding-vision（多模态向量化，dimensions 可选 1024 / 2048）。
本项目采用 2048 维。

维度从 2560 降到 2048，旧向量无法兼容，迁移时清空分块数据；
资讯会因「EMBEDDED 但无分块」被 sweep 命令打回重新向量化。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TARGET_DIM = 2048
LEGACY_DIM = 2560


def upgrade() -> None:
    op.drop_index("idx_chunk_embedding", table_name="news_chunk")
    # 2560 → 2048 无法自动转换，旧向量作废，清空后重新向量化
    op.execute("DELETE FROM news_chunk")
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
    op.execute("DELETE FROM news_chunk")
    op.execute(f"ALTER TABLE news_chunk ALTER COLUMN embedding TYPE halfvec({LEGACY_DIM})")
    op.create_index(
        "idx_chunk_embedding",
        "news_chunk",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_with={"m": "16", "ef_construction": "64"},
        postgresql_ops={"embedding": "halfvec_cosine_ops"},
    )


# 保留 sa 引用：便于后续手工扩展本迁移（如分表回填）
_ = sa
