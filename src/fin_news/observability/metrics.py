"""Agent 运行指标查询层。

把指标口径收在一处，供 CLI / 未来的 Web 面板与 API 复用，避免同一段聚合 SQL
散落多处后各写各的、口径漂移。

与 `v_agent_daily` / `v_llm_daily` 两个视图的分工
-------------------------------------------------
* 视图是**日粒度**，用于看趋势（每天多少、怎么变化）与手工 SQL 排查
* 本模块查**原始表**，用于「近 N 天汇总」—— 因为分位数（P50 / P95）无法从按天
  聚合的结果准确还原，跨天汇总必须回到原始行上算，否则是把各天的 P95 再取一次
  分位数，数学上不成立
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from fin_news.agents.llm.pricing import is_priced
from fin_news.core.timeutil import now_utc


@dataclass
class AgentHealthRow:
    """单个 Agent 在指定时间窗内的健康汇总。"""

    agent_type: str
    runs: int
    ok_runs: int
    degraded_runs: int
    failed_runs: int
    ok_rate: float  # 百分比
    degraded_rate: float
    failed_rate: float
    p50_ms: int
    p95_ms: int
    max_ms: int
    avg_prompt_tokens: int
    avg_completion_tokens: int
    cost_cent_total: float


@dataclass
class LLMSummaryRow:
    """模型调用维度（role × model）汇总。

    作用：agent_run 是新建的、没有历史数据，靠 llm_call_log（已有数千行）兜底，
    保证监控面板上线当天就有东西可看，而不是一片空白。
    """

    role: str
    model: str
    calls: int
    errors: int
    error_rate: float
    avg_latency_ms: int
    p95_ms: int
    prompt_tokens: int
    completion_tokens: int
    cost_cent_total: float
    estimated_calls: int  # token 来自字符估算的行数；占比高说明用量数据不可信
    priced: bool  # 是否命中单价配置；False 表示成本是兜底估算，需补 model_pricing


@dataclass
class ReportQualityRow:
    """分析报告 / 简报的质量分布（PUBLISHED / DEGRADED / SUPERSEDED）。"""

    agent_type: str
    status: str
    n: int


_AGENT_HEALTH_SQL = text(
    """
    SELECT
        agent_type,
        count(*)                                                             AS runs,
        count(*) FILTER (WHERE status = 'SUCCESS' AND NOT degraded)          AS ok_runs,
        count(*) FILTER (WHERE degraded)                                     AS degraded_runs,
        count(*) FILTER (WHERE status IN ('FAILED','TIMEOUT','DEAD','CANCELLED'))
                                                                             AS failed_runs,
        COALESCE(percentile_cont(0.5)  WITHIN GROUP (ORDER BY latency_ms), 0) AS p50_ms,
        COALESCE(percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms), 0) AS p95_ms,
        COALESCE(max(latency_ms), 0)                                         AS max_ms,
        COALESCE(round(avg(COALESCE(prompt_tokens, 0))), 0)                  AS avg_prompt_tokens,
        COALESCE(round(avg(COALESCE(completion_tokens, 0))), 0)              AS avg_completion_tokens,
        COALESCE(round(sum(COALESCE(cost_cent, 0))::numeric, 2), 0)          AS cost_cent_total
    FROM agent_run
    WHERE finished_at >= :since
    GROUP BY agent_type
    ORDER BY runs DESC, agent_type
    """
)

_LLM_SUMMARY_SQL = text(
    """
    SELECT
        COALESCE(role, '-')  AS role,
        COALESCE(model, '-') AS model,
        count(*)                                                             AS calls,
        count(*) FILTER (WHERE status <> 'OK')                               AS errors,
        COALESCE(round(avg(latency_ms)), 0)                                  AS avg_latency_ms,
        COALESCE(percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms), 0) AS p95_ms,
        COALESCE(sum(COALESCE(prompt_tokens, 0)), 0)                         AS prompt_tokens,
        COALESCE(sum(COALESCE(completion_tokens, 0)), 0)                     AS completion_tokens,
        COALESCE(round(sum(COALESCE(cost_cent, 0))::numeric, 2), 0)          AS cost_cent_total,
        count(*) FILTER (WHERE estimated)                                    AS estimated_calls
    FROM llm_call_log
    WHERE created_at >= :since
    GROUP BY COALESCE(role, '-'), COALESCE(model, '-')
    ORDER BY calls DESC
    """
)

_REPORT_QUALITY_SQL = text(
    """
    SELECT agent_type, status, count(*) AS n
    FROM analysis_report
    GROUP BY agent_type, status
    ORDER BY agent_type, n DESC
    """
)


def _rate(num: int, den: int) -> float:
    return round(100.0 * num / den, 1) if den else 0.0


async def agent_health(session: AsyncSession, *, days: int = 7) -> list[AgentHealthRow]:
    """近 N 天各 Agent 的成功 / 降级 / 失败率、延迟分位与成本。"""
    since = now_utc() - timedelta(days=days)
    rows = (await session.execute(_AGENT_HEALTH_SQL, {"since": since})).all()

    out: list[AgentHealthRow] = []
    for r in rows:
        runs = int(r.runs or 0)
        ok = int(r.ok_runs or 0)
        deg = int(r.degraded_runs or 0)
        fail = int(r.failed_runs or 0)
        out.append(
            AgentHealthRow(
                agent_type=str(r.agent_type),
                runs=runs,
                ok_runs=ok,
                degraded_runs=deg,
                failed_runs=fail,
                ok_rate=_rate(ok, runs),
                degraded_rate=_rate(deg, runs),
                failed_rate=_rate(fail, runs),
                p50_ms=int(r.p50_ms or 0),
                p95_ms=int(r.p95_ms or 0),
                max_ms=int(r.max_ms or 0),
                avg_prompt_tokens=int(r.avg_prompt_tokens or 0),
                avg_completion_tokens=int(r.avg_completion_tokens or 0),
                cost_cent_total=float(r.cost_cent_total or 0),
            )
        )
    return out


async def llm_summary(session: AsyncSession, *, days: int = 7) -> list[LLMSummaryRow]:
    """近 N 天模型调用汇总（role × model），用于 agent_run 无历史数据时兜底。"""
    since = now_utc() - timedelta(days=days)
    rows = (await session.execute(_LLM_SUMMARY_SQL, {"since": since})).all()

    out: list[LLMSummaryRow] = []
    for r in rows:
        calls = int(r.calls or 0)
        errors = int(r.errors or 0)
        out.append(
            LLMSummaryRow(
                role=str(r.role),
                model=str(r.model),
                calls=calls,
                errors=errors,
                error_rate=_rate(errors, calls),
                avg_latency_ms=int(r.avg_latency_ms or 0),
                p95_ms=int(r.p95_ms or 0),
                prompt_tokens=int(r.prompt_tokens or 0),
                completion_tokens=int(r.completion_tokens or 0),
                cost_cent_total=float(r.cost_cent_total or 0),
                estimated_calls=int(r.estimated_calls or 0),
                priced=is_priced(str(r.model)),
            )
        )
    return out


async def report_quality(session: AsyncSession) -> list[ReportQualityRow]:
    """报告质量分布：PUBLISHED（正常） / DEGRADED（降级） / SUPERSEDED（被新版本取代）。"""
    rows = (await session.execute(_REPORT_QUALITY_SQL)).all()
    return [
        ReportQualityRow(agent_type=str(r.agent_type), status=str(r.status), n=int(r.n or 0))
        for r in rows
    ]
