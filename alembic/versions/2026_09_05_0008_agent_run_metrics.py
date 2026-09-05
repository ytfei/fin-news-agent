"""Agent 运行指标：agent_run 加 degraded 列 + 两个聚合视图。

背景
----
`agent_run` 表建好了但从未写入（线上 0 行），Agent 级别的成功率 / 延迟 / 成本
完全不可见。本迁移补上「降级」标记列，并建立两个日粒度聚合视图，让 CLI、
未来的 Web 面板与手工 SQL 共用同一套指标口径（避免各处各写一段 SQL 导致漂移）。

关于视图而非物化视图
--------------------
agent_run 每天量级在几百到几千行，普通视图足够快；物化视图还要额外维护刷新
策略，属过度设计。
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# 按 Asia/Shanghai 的自然日分组（与项目业务时区一致，见 core/timeutil.py）
def upgrade() -> None:
    op.add_column(
        "agent_run",
        sa.Column("degraded", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )

    op.execute(
        """
        CREATE VIEW v_agent_daily AS
        SELECT
            agent_type,
            (finished_at AT TIME ZONE 'Asia/Shanghai')::date          AS day,
            count(*)                                                  AS runs,
            count(*) FILTER (WHERE status = 'SUCCESS' AND NOT degraded) AS ok_runs,
            count(*) FILTER (WHERE degraded)                          AS degraded_runs,
            count(*) FILTER (WHERE status IN ('FAILED', 'TIMEOUT', 'DEAD', 'CANCELLED'))
                                                                      AS failed_runs,
            CASE WHEN count(*) > 0 THEN round(
                100.0 * count(*) FILTER (WHERE status = 'SUCCESS' AND NOT degraded) / count(*), 1)
            END                                                       AS ok_rate,
            CASE WHEN count(*) > 0 THEN round(
                100.0 * count(*) FILTER (WHERE degraded) / count(*), 1)
            END                                                       AS degraded_rate,
            CASE WHEN count(*) > 0 THEN round(
                100.0 * count(*) FILTER (
                    WHERE status IN ('FAILED', 'TIMEOUT', 'DEAD', 'CANCELLED')) / count(*), 1)
            END                                                       AS failed_rate,
            percentile_cont(0.5)  WITHIN GROUP (ORDER BY latency_ms)  AS p50_ms,
            percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)  AS p95_ms,
            max(latency_ms)                                           AS max_ms,
            round(avg(coalesce(prompt_tokens, 0)))::bigint            AS avg_prompt_tokens,
            round(avg(coalesce(completion_tokens, 0)))::bigint        AS avg_completion_tokens,
            round(sum(coalesce(cost_cent, 0))::numeric, 2)            AS cost_cent_total
        FROM agent_run
        WHERE finished_at IS NOT NULL
        GROUP BY agent_type, (finished_at AT TIME ZONE 'Asia/Shanghai')::date
        """
    )

    # llm_call_log 维度的历史视图：agent_run 从 0 开始，靠它兜底让面板上线即有数据可看
    op.execute(
        """
        CREATE VIEW v_llm_daily AS
        SELECT
            (created_at AT TIME ZONE 'Asia/Shanghai')::date   AS day,
            coalesce(role, '-')                               AS role,
            coalesce(model, '-')                              AS model,
            count(*)                                          AS calls,
            count(*) FILTER (WHERE status <> 'OK')            AS errors,
            CASE WHEN count(*) > 0 THEN round(
                100.0 * count(*) FILTER (WHERE status <> 'OK') / count(*), 1)
            END                                               AS error_rate,
            round(avg(latency_ms))::bigint                    AS avg_latency_ms,
            percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_ms,
            sum(coalesce(prompt_tokens, 0))::bigint           AS prompt_tokens,
            sum(coalesce(completion_tokens, 0))::bigint       AS completion_tokens,
            round(sum(coalesce(cost_cent, 0))::numeric, 2)    AS cost_cent_total,
            count(*) FILTER (WHERE estimated)                 AS estimated_calls
        FROM llm_call_log
        GROUP BY
            (created_at AT TIME ZONE 'Asia/Shanghai')::date,
            coalesce(role, '-'),
            coalesce(model, '-')
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_llm_daily")
    op.execute("DROP VIEW IF EXISTS v_agent_daily")
    op.drop_column("agent_run", "degraded")
