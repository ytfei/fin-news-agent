"""news_status 枚举新增 EXPIRED：超时效窗口的资讯不再自动分析。

背景
----
时效策略（见 docs/09 相关设计）：`publish_time` 距今超过 `analysis_max_age_hours`
的资讯，事件被定时任务批量 ACK，资讯本身标记为 `EXPIRED`，改由用户在
Web / Mobile 手动触发「生成报告」。

关于 ALTER TYPE ADD VALUE 的事务约束
------------------------------------
PG 的 `ALTER TYPE ... ADD VALUE` 有两个约束：
1. 在 PG 12 之前，该语句不能在事务块中执行；
2. 即使能执行，新值也不能在**同一个事务**里被使用。

本项目运行于 PG 16，且本迁移只新增枚举值、不立即写入 EXPIRED 记录，因此
可以直接在 Alembic 的默认事务里执行（满足约束 2）。若未来降到 PG 11 及以下，
需先 `op.execute("COMMIT")` 结束隐式事务再执行本语句。

关于 downgrade
--------------
PG 不提供 `ALTER TYPE ... DROP VALUE`，删除枚举值唯一安全的方式是：
建新类型 → 迁移所有引用列 → 删旧类型。这涉及多张表（news_item 及未来可能
的引用列），风险高、收益低，且 EXPIRED 一旦被业务写入，回滚会连带丢失
状态语义。故本迁移不可逆，downgrade 仅留注释说明。
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # PG 12+ 允许在事务块内执行 ALTER TYPE ADD VALUE（唯一限制：新值不能在同一
    # 事务中被使用）。本迁移只新增值、不立即写入 EXPIRED 记录，故可直接在
    # Alembic 默认事务里执行。若未来降级到 PG 11 及以下，需先 op.execute("COMMIT")
    # 结束隐式事务再执行本语句。
    op.execute("ALTER TYPE news_status ADD VALUE IF NOT EXISTS 'EXPIRED'")


def downgrade() -> None:
    # PG 不支持 DROP VALUE；删除枚举值需重建类型，风险高且本迁移属业务必需，
    # 故不提供自动回滚。若确需回退，需手动重建 news_status 类型并更新引用列。
    pass
