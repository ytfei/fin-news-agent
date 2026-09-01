"""uq_report_brief 增加 status 过滤条件

原因：`analysis_report` 的简报唯一索引原定义为
`(trade_date, period, prompt_version) WHERE period IN ('pre_market','post_market')`，
**缺少 status 过滤**。而重跑简报时 `_persist_brief` 会先把同版本旧报告标为
SUPERSEDED 再插入新报告——旧记录虽已 SUPERSEDED，却仍占据唯一索引，导致
第二次生成同一交易日简报直接 UniqueViolationError 回滚。

与 `uq_report_news_agent`（资讯分析，含 status 过滤）保持一致：
只有生效中的报告（DRAFT/PUBLISHED/DEGRADED）才受唯一约束。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WHERE = (
    "period IN ('pre_market','post_market')"
    " AND status IN ('DRAFT','PUBLISHED','DEGRADED')"
)


def upgrade() -> None:
    op.drop_index("uq_report_brief", table_name="analysis_report")
    op.create_index(
        "uq_report_brief",
        "analysis_report",
        ["trade_date", "period", "prompt_version"],
        unique=True,
        postgresql_where=_WHERE,
    )


def downgrade() -> None:
    op.drop_index("uq_report_brief", table_name="analysis_report")
    op.create_index(
        "uq_report_brief",
        "analysis_report",
        ["trade_date", "period", "prompt_version"],
        unique=True,
        postgresql_where="period IN ('pre_market','post_market')",
    )


_ = sa
