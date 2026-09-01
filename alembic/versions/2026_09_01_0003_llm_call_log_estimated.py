"""llm_call_log 增加 estimated 列。

LangChain 回调取不到 provider usage 时会按字符粗估 token，需要标记出来，
避免成本看板把估算值当成真实值。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "llm_call_log",
        sa.Column("estimated", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("llm_call_log", "estimated")
