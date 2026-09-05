"""Agent 运行埋点：把每次 Agent 执行写入 `agent_run`。

为什么需要它
------------
`agent_run` 表结构完善（run_id / agent_type / status / latency_ms / token /
cost_cent / error_type / started_at / finished_at），但**从未被写入过**（线上实测
0 行）。后果是完全看不到 Agent 级别的运行状况：只能从 `llm_call_log` 按 role
（scoring / analysis / qa / embedding）粗粒度统计，无法区分宏观 / 行业 / 个股 /
盘前 / 盘后各自的成功率、延迟与成本。

埋点位置
--------
深度分析与简报的真实执行入口是 `analysis_agents.analyze_news` 与
`market_agents._build_brief`。注意 `agents/base.py` 里的 `run_agent` 已无任何
调用点，是历史遗留死代码，不要在那里埋点。

run_id 如何传到深层
-------------------
模型调用发生在深层调用栈（graph → ChatModel → callback），逐层透传 run_id 会
污染大量函数签名。这里改用 structlog 的 contextvars：进入时绑定 run_id，
落库侧（AuditCallbackHandler / LLMClient / Embedder）用 `current_run_id()` 读取。
contextvars 在 asyncio 下按 task 隔离，因此并发的多个 Agent 运行不会串扰。

幂等
----
复用 `agent_run` 已有的 `uq_run_idem` 唯一索引（agent_type + subject_id +
prompt_version + input_digest）：同一输入重跑时更新同一条并递增 attempt，
避免重试把「运行数」灌水。subject_id 为空时该索引在 PG 中不生效（NULL 互不
相等），即无主体的运行不去重。
"""
from __future__ import annotations

import hashlib
import time
import uuid
from types import TracebackType
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert

from fin_news.agents.llm.pricing import calc_cost_cent
from fin_news.core.db import session_scope
from fin_news.core.enums import AgentType, RunStatus
from fin_news.core.logging import bind_context, get_logger, unbind_context
from fin_news.core.timeutil import now_utc
from fin_news.models.event import AgentRun

logger = get_logger("observability.tracker")

# 幂等键（对应 agent_run 的 uq_run_idem 唯一索引）
_IDEM_COLS = ("agent_type", "subject_id", "prompt_version", "input_digest")

_MAX_ERROR_CHARS = 500


def digest_of(*parts: str) -> str:
    """计算输入指纹，用于运行去重。

    只把「影响输出的关键输入」拼进来（通常是最终 user_prompt）。
    切勿放入时间戳或随机值，否则去重失效。
    """
    joined = "\x00".join(p or "" for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:32]


class AgentRunTracker:
    """一次 Agent 运行的埋点上下文（异步上下文管理器）。

    用法::

        async with AgentRunTracker(
            agent_type,
            subject_type="news",
            subject_id=str(news.id),
            prompt_version=version,
            input_digest=digest_of(user_prompt),
        ) as run:
            output = await _run_analysis(...)
            run.finish(output)

    契约：
    * **绝不阻断主流程** —— 埋点自身的任何异常都被吞掉并记 warning
    * 未调用 finish() 就退出时，按是否抛异常记为 FAILED / CANCELLED，不留悬空记录
    * 进入时把 run_id 绑定到 contextvars，退出时解绑
    """

    def __init__(
        self,
        agent_type: AgentType,
        *,
        subject_type: str = "",
        subject_id: str | None = None,
        prompt_version: str = "",
        input_digest: str = "",
        model: str = "",
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.agent_type = agent_type
        self.subject_type = subject_type
        self.subject_id = subject_id
        self.prompt_version = prompt_version
        self.input_digest = input_digest
        self.model = model
        self.payload: dict[str, Any] = payload or {}

        self.run_id = uuid.uuid4().hex
        self._started_at = now_utc()
        self._started = 0.0
        self._output: Any = None
        self._status: RunStatus | None = None

    # ------------------------------------------------------------------
    async def __aenter__(self) -> AgentRunTracker:
        self._started_at = now_utc()
        self._started = time.perf_counter()
        bind_context(run_id=self.run_id)
        return self

    def finish(self, output: Any, *, status: RunStatus = RunStatus.SUCCESS) -> None:
        """记录本次运行的产出（同步方法；真正落库发生在 `__aexit__`）。"""
        self._output = output
        self._status = status

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        try:
            if exc is not None:
                await self._write(RunStatus.FAILED, error=exc)
            elif self._status is not None:
                await self._write(self._status)
            else:
                # 既没 finish 也没异常：通常是被上层吞掉的失败，别留悬空记录
                await self._write(RunStatus.CANCELLED)
        except Exception as write_exc:  # noqa: BLE001 - 埋点绝不能阻断主流程
            logger.warning(
                "Agent 运行埋点写入失败",
                agent=self.agent_type.value,
                run_id=self.run_id,
                error=str(write_exc)[:200],
            )
        finally:
            unbind_context("run_id")
        return False  # 不吞异常

    # ------------------------------------------------------------------
    async def _write(self, status: RunStatus, *, error: BaseException | None = None) -> None:
        latency_ms = int((time.perf_counter() - self._started) * 1000)
        output = self._output

        # 用 getattr 取值：降级路径（_run_plain_agent）与图路径返回的都是 AgentOutput，
        # 但保持弱耦合，避免某个路径换类型时埋点直接抛异常
        degraded = bool(getattr(output, "degraded", False))
        prompt_tokens = int(getattr(output, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(output, "completion_tokens", 0) or 0)
        model = str(getattr(output, "model", "") or self.model or "")
        cost = calc_cost_cent(model, prompt_tokens, completion_tokens) if model else 0.0

        error_message = (
            f"{type(error).__name__}: {error}"[:_MAX_ERROR_CHARS] if error else None
        )

        values: dict[str, Any] = {
            "run_id": self.run_id,
            "agent_type": self.agent_type,
            "subject_type": self.subject_type or "",
            "subject_id": self.subject_id,
            "status": status,
            "degraded": degraded,
            "model": model or None,
            "prompt_version": self.prompt_version or None,
            "input_digest": self.input_digest or None,
            "payload": self.payload,
            "latency_ms": latency_ms,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cost_cent": cost,
            "error_type": type(error).__name__ if error else None,
            "error_message": error_message,
            # 未接外部追踪系统前，用 run_id 兼作 trace_id，保证日志与埋点能互查
            "trace_id": self.run_id,
            "scheduled_at": self._started_at,
            "started_at": self._started_at,
            "finished_at": now_utc(),
        }

        # 同输入重跑 → 更新同一条并递增 attempt（避免重试灌水「运行数」）
        stmt = pg_insert(AgentRun).values(**values).on_conflict_do_update(
            index_elements=list(_IDEM_COLS),
            set_={
                "run_id": self.run_id,
                "status": status,
                "degraded": degraded,
                "model": values["model"],
                "latency_ms": latency_ms,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cost_cent": cost,
                "error_type": values["error_type"],
                "error_message": error_message,
                "finished_at": values["finished_at"],
                "attempt": AgentRun.attempt + 1,
            },
        )
        async with session_scope() as session:
            await session.execute(stmt)
