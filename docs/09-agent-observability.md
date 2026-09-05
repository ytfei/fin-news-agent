# 09 · Agent 可观测性与评估方案

> 目标：让 8 个 Agent（评分 / 宏观 / 行业 / 个股 / 盘前 / 盘后 / 写文章 / 追问）的运行状况
> **看得见、能定位、可归因**。
>
> 本文分四部分：①业界主流 Agent 评估方案 ②分层指标清单 ③指标异常时的优化预案
> ④本轮已落地的 P0 最小闭环。

---

## 一、业界主流 Agent 评估方案

### 1.1 三层评估体系（诊断栈）

业界共识是把 Agent 评估分成三层，构成一个自上而下的诊断栈：**先看结果，再看路径，最后定位组件**。

| 层次 | 回答什么问题 | 代表指标 |
| --- | --- | --- |
| **End-to-end**（端到端，黑盒） | 任务完成了吗？ | Task Completion |
| **Trajectory-level**（轨迹级） | 达成目标的**路径**高效吗？合理吗？ | Step Efficiency、Plan Adherence、Plan Quality |
| **Component-level**（组件级） | 到底**哪个部件**坏了？ | Tool Correctness、Argument Correctness、RAG 指标、子 Agent |

为什么必须分层：Agent 的错误会**复合传播**——一个糟糕的早期假设会污染后续每一步，
最终暴露的失败点往往远离真正的错误源头。端到端只能回答「坏了」，组件级才能回答「哪坏了」。

> 典型反例：Agent 每一步工具调用看起来都合理，最终答案也对，但它绕了 12 步、调用了 3 个
> 无关工具、烧了 10 倍 token。**只看端到端会误判为优秀**。

### 1.2 事实标准：OpenTelemetry GenAI 语义约定

当前版本 v1.41，状态为 **Development**（属性名仍可能变更），但已可用于生产。

**必须导出的两个核心指标：**

| 指标 | 含义 |
| --- | --- |
| `gen_ai.client.operation.duration` | 每次操作的延迟（秒） |
| `gen_ai.client.token.usage` | Token 消耗（区分 input / output） |

**四种核心 Span（构成调用树）：**

```
invoke_workflow (INTERNAL)          ← 顶层编排
  └── invoke_agent (INTERNAL)       ← 单个 Agent
        ├── chat (CLIENT)           ← 模型调用
        └── execute_tool (INTERNAL) ← 工具调用
```

**关键属性：** `gen_ai.request.model`、`gen_ai.usage.input_tokens`、
`gen_ai.usage.output_tokens`、`gen_ai.response.finish_reason`、`gen_ai.tool.name`、`gen_ai.agent.name`

**两条必须遵守的工程原则：**

1. **埋点代码按 OTel 规范写，而不是按厂商私有 SDK 写。** 这样未来换后端
   （自建 → Langfuse → Braintrust）只改 Exporter，不改埋点。
   > 本项目本轮自建，但字段命名已向规范靠拢：`llm_call_log.prompt_tokens` /
   > `completion_tokens` 对应 `gen_ai.usage.*`，后续加字段应直接采用规范名。

2. **绝对不要硬编码模型价格。** 厂商调价频繁，价格必须外部化配置。
   > 这正是本项目踩过的坑，见 §4.2。

### 1.3 指标矩阵与判定方式

| 指标 | 衡量什么 | 判定方式 | 层次 |
| --- | --- | --- | --- |
| Task Completion | 是否达成用户目标 | LLM judge | 端到端 |
| Step Efficiency | 是否避免多余步骤 / 重试 / 空转循环 | LLM judge | 轨迹级 |
| **Tool Correctness** | 是否调用了正确的工具 | **确定性** | 组件级 |
| Argument Correctness | 工具入参是否正确 | LLM judge | 组件级 |
| Plan Quality | 计划是否完整、现实、高效 | LLM judge | 轨迹级 |
| Plan Adherence | 执行是否偏离计划 | LLM judge | 轨迹级 |
| Reasoning Relevancy / Coherence | 推理是否切题、连贯 | LLM judge | 组件级 |
| RAG 五件套（Answer Relevancy / Faithfulness / Contextual Precision / Recall / Relevancy） | 检索质量与 grounded 程度 | LLM judge | 组件级 + 端到端 |
| Safety（Bias / Toxicity / Harmful） | 输出安全 | LLM judge | 任意 |
| G-Eval | 任意自然语言定义的自定义标准 | LLM judge | 任意 |

**经验法则：能用确定性判定的（工具名、必填参数、期望输出）绝不用 LLM judge；
只有需要判断、需要上下文的才用 LLM-as-a-judge。** 前者零成本零方差，后者才有必要。

### 1.4 工具生态横评（本轮不引入，作为演进方向）

| 方案 | 定位 | 免费额度 | 备注 |
| --- | --- | --- | --- |
| **Langfuse** | 开源自托管，最活跃 | MIT 自托管免费 | 原生 OTel、自带 Prompt 管理与数据集；**数据主权优先时首选** |
| **LangSmith** | SaaS，上手最快 | 5K traces/月 | 内置 NL trace 分析 |
| **Arize Phoenix** | 开源，检索评估强 | Elastic License 2.0 | 基于 OpenInference，擅长 embedding 可视化 |
| **Braintrust** | SaaS，自动评分器 | 1M spans/月 | 内置评分器 |
| **Helicone** | 代理网关，零代码接入 | 10K 请求/月 | 有单点故障风险 |
| **Datadog LLM Obs** | 企业 APM 扩展 | 40K LLM spans/月 | **仅对 LLM Span 计费**，工具密集型场景可能更便宜 |

最小可行栈（业界推荐优先级）：①追踪 → ②结构化日志带 trace_id → ③成本 gauge
→ ④时长 histogram → ⑤告警。

### 1.5 结合本项目的适用性裁剪

并非所有指标都值得做，按本项目特点裁剪：

| 指标 | 是否适用 | 理由 |
| --- | --- | --- |
| Tool Correctness / Argument Correctness | **高度适用** | 是「工具调用 + 长链路」型 Agent（ReAct 多轮、子 Agent 并行） |
| Step Efficiency | **高度适用** | 深度分析单次运行可达数十步，空转会直接烧钱 |
| 降级率 / 超时率 | **高度适用** | 已有降级机制（`use_deep_agents` 失败回退单次调用），但此前**无人监控** |
| RAG 指标（检索质量） | 适用 | 有 `history_search` / `article_search` 等检索工具 |
| Plan Quality / Plan Adherence | 后置 | Agent 不显式产出计划，收益有限 |
| Safety（Bias / Toxicity） | **优先级低** | 本项目 Agent **不执行任何外部写操作**（不改库、不发消息、不提交代码），风险面小 |

---

## 二、分层指标清单

### P0 · 最关键 —— 回答「能不能用」

> 本轮已全部落地，见 `fin_news.cli status` 的「五、Agent 健康」面板。

| 指标 | 口径 | 数据源 | 健康阈值建议 |
| --- | --- | --- | --- |
| **Agent 成功率** | `status='SUCCESS' AND NOT degraded` 的运行数 / 总运行数 | `agent_run` | ≥ 95% |
| **降级率** | `degraded` 的运行数 / 总运行数 | `agent_run` | ≤ 10% |
| **失败率** | `status IN (FAILED,TIMEOUT,DEAD,CANCELLED)` / 总运行数 | `agent_run` | ≤ 3% |
| **延迟 P50 / P95** | `latency_ms` 分位数，按 agent_type | `agent_run` | 分析 P95 ≤ 300s（=超时线）、评分 P95 ≤ 60s |
| **单条成本** | 单次运行的 `cost_cent`，按 agent_type | `agent_run` | 与模型基线对齐 |
| **日成本** | 按天 × model 汇总 `cost_cent` | `llm_call_log` | 环比波动 ±30% 预警 |
| **简报质量分布** | PUBLISHED / DEGRADED / SUPERSEDED 占比 | `analysis_report` | 降级率 ≤ 20% |
| **链路积压** | PENDING / overdue 事件数 | `ingest_event` | overdue 应为 0 |

手工排查 SQL（可直接跑）：

```sql
-- 各 Agent 近 7 天健康汇总
SELECT agent_type,
       count(*) AS runs,
       round(100.0 * count(*) FILTER (WHERE status='SUCCESS' AND NOT degraded) / count(*), 1) AS ok_rate,
       round(100.0 * count(*) FILTER (WHERE degraded) / count(*), 1)                          AS degraded_rate,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)                               AS p95_ms,
       round(sum(COALESCE(cost_cent,0))::numeric, 2)                                          AS cost_cent
FROM agent_run
WHERE finished_at >= now() - interval '7 days'
GROUP BY agent_type ORDER BY runs DESC;

-- 按天趋势（视图，已建好）
SELECT * FROM v_agent_daily ORDER BY day DESC, agent_type;
SELECT * FROM v_llm_daily  ORDER BY day DESC, calls DESC;
```

### P1 · 次要 —— 回答「好不好用」

| 指标 | 口径 | 数据源 | 阈值建议 |
| --- | --- | --- | --- |
| **工具调用失败率** | 工具 error 次数 / 工具调用次数 | **当前仅存在于日志**（`StepTraceHandler` 只打日志不落库） | ≤ 5% |
| **重试率** | `attempt > 1` 的运行占比 | `agent_run.attempt` | ≤ 10% |
| **错误归因分布** | 按 `error_type` 分组计数 | `agent_run` | 无单一类型占比过半 |
| **Token 效率** | 单条平均 input / output token | `llm_call_log` | 观察环比；`estimated` 占比应≈0 |
| **评分一致率** | `band_agree_rate`（人工 vs 模型） | `score_eval_set`（已有） | ≥ 80%（PRD 验收口径） |
| **可下钻率** | 每次运行能用 `run_id` 关联到全部 LLM 调用 | `agent_run` ← → `llm_call_log` | 100% |

> **当前最大盲区**：工具调用（tool call）指标。`StepTraceHandler` 会把每一步的
> 工具名、入参、耗时打到日志，但**不落库**，因此无法聚合出「哪个工具最爱失败 / 最慢」。
> 落地方式：在 `on_tool_end` / `on_tool_error` 里落一张 `agent_tool_call` 表即可，不需要改业务逻辑。

### P2 · 锦上添花 —— 明确暂不做，仅登记

- **步骤效率**：每次运行的 LLM 往返次数（判断空转循环）
- **成本归因到渠道**：把成本分摊到财联社 / 华尔街见闻 / 第一财经
- **漂移检测**：对比本周与上周的分数分布、输出长度分布
- **缓存命中率**：重复 prompt 的复用率
- **人工反馈闭环**：把人工修正回流成评估集样本

---

## 三、改进与优化预案

每个异常信号的排查路径与具体调参动作（参数名均为 `config.py` 中的真实字段）。

| 异常信号 | 排查路径 | 调参 / 改进动作 | 预期效果与副作用 |
| --- | --- | --- | --- |
| **降级率高** | 查 `agent_run` 里降级记录的 `error_type` 分布：超时 / 结构化输出失败 / 图执行异常 | 超时为主：`analysis_timeout_seconds` 300→600，或收紧 `agent_recursion_limit`（200→更低）防空转；临时设 `use_deep_agents=False` 对比，确认是否为深度链路本身的问题 | 放宽超时会拉长最坏耗时、抬高成本；收紧步数可能导致复杂任务做不完，需同步观察成功率 |
| **失败率高** | 查 `llm_call_log.error_message` 分布，区分 429 限流 / 超时 / JSON 解析失败 | 429 为主：降 `analysis_concurrency`（4→2），或引入类似 `embedding_qps` 的 QPS 闸门；超时为主：调 `llm_timeout_seconds`（120）；解析失败为主：加固 `parse_json_content` 并在 prompt 里强化 schema 约束 | 降并发会拉低吞吐，需与积压指标联动看，避免治好失败率却堆出积压 |
| **延迟 P95 高** | 按 agent_type 拆开：是评分慢还是分析慢？再看是模型慢还是工具慢 | 评分慢：调小 `scoring_sub_batch_size`（5→3），小批返回更快；分析慢：调 `analysis_concurrency`；确认评分走轻量模型 `volcengine_model_scoring`、只有分析走强模型 | 批次拆细会增加调用次数与固定开销，需同时看成本指标 |
| **成本超预算** | 按 agent_type 与 model 拆成本，定位烧钱环节 | 调高 `score_threshold_vectorize`（3→4）减少进入分析的资讯量；对低分内容直接归档；确认评分走小模型 | 提高阈值会漏掉部分中等价值资讯，需结合评分一致率与人工抽查确认未误杀 |
| **质量差（DEGRADED 多）** | 抽降级样本，用 `run_id` 关联日志看卡在哪一步；对比降级与正常输出的差异 | 用已就绪的 `prompt_template` 版本化做 A/B；补充 few-shot；长文本调 `scoring_max_content_chars` | 改 prompt 必须回归验证（本轮不动评估集，改 prompt 时人工抽查） |
| **积压上涨** | 查 `ingest_event` 的 PENDING / overdue 分布与 `attempts` | 调 `worker_batch_limit`、`worker_poll_interval_seconds`；降低 `event_backoff_base_seconds`（30）加快重试；必要时扩 worker | 加快重试会放大对下游 LLM 的压力，需与失败率联动 |
| **token / 成本突增** | 对比同 agent_type 的历史 token 均值 | 检查是否陷入重复工具调用或循环：收紧 `agent_recursion_limit`；开 `agent_trace_enabled` 看步骤日志 | 收紧步数可能影响复杂任务完成度 |
| **成本数值不可信** | 看面板底部是否有「未配置单价」提示 | 在 `.env` 的 `MODEL_PRICING` 补齐该模型单价（元/百万 token，分 input/output） | 单价是配置而非代码，改完立即生效；历史成本可用 `cost-recalc` 重算 |

---

## 四、本轮 P0 落地说明

### 4.1 做了什么

| 改动 | 文件 | 说明 |
| --- | --- | --- |
| 启用 `agent_run` 埋点 | `observability/tracker.py`（新） | 埋在 `analyze_news()` 与 `_build_brief()`，覆盖 5 个 Agent |
| 降级标记独立成列 | `models/event.py` + 迁移 `0008` | `agent_run.degraded`，便于 SQL 直接聚合 |
| 统一计价 | `agents/llm/pricing.py`（新） | 消除散落 3 处的假单价表，按 model 名 + input/output 计价 |
| 单价可配置 | `core/config.py` | 新增 `model_pricing`，支持环境变量覆盖 |
| 指标查询层 | `observability/metrics.py`（新） | 口径收在一处，供 CLI / 未来 Web 复用 |
| 聚合视图 | 迁移 `0008` | `v_agent_daily`、`v_llm_daily`（日粒度，看趋势与手工排查） |
| 监控面板 | `cli.py` | `status` 新增「五、Agent 健康」 |

### 4.2 关键实现决策（与最初设想不同的地方，值得记录）

1. **`agents/base.py` 的 `run_agent()` 是死代码** —— 全库无任何调用点。
   真实的分析执行入口是 `analysis_agents.analyze_news()`（逐条资讯）与
   `market_agents._build_brief()`（盘前盘后简报），埋点必须插在这两处。

2. **`run_id` 用 `structlog.contextvars` 透传，而非逐层传参。**
   模型调用发生在深层（graph → ChatModel → callback），逐层加 `run_id` 参数会污染大量函数签名。
   更关键的是：`ModelFactory` **缓存**了 ChatModel 实例，`AuditCallbackHandler` 挂在其上被
   所有 run 共享，构造时传 run_id 根本无法区分调用方。
   改用 contextvars 后，共享 handler 在落库时自动读取当前 task 的 run_id —— 并发下按 task 隔离，安全。

3. **token 直接取 `AgentOutput`，不从 `llm_call_log` 聚合。**
   原计划担心 DeepAgents 路径取不到 token 会记 0；实测该担忧不成立——生效路径
   `run_analysis()` 的 `_usage_of()` 会**累计所有 AIMessage** 的 `usage_metadata`，数据准确
   （实测 3264 行中仅 8 行 `estimated=true`，且都是 ERROR 行）。

4. **`agent_run` 用 upsert 而非 insert。**
   复用表上已有的 `uq_run_idem`（agent_type + subject_id + prompt_version + input_digest）：
   同输入重跑时更新同一条并递增 `attempt`，否则重跑会把「运行数」灌水、污染成功率。
   已验证：同一条资讯连跑两次，`agent_run` 仍为 1 行、`attempt=2`。

5. **单价表必须覆盖真实模型名。**
   实测发现 `.env` 实际用的是 `doubao-seed-2.0-mini` / `doubao-seed-evolving`，
   与 `config.py` 的默认值（`doubao-lite-32k` / `doubao-pro-32k`）**完全不同**；
   且线上还存在带日期后缀的快照版（`doubao-seed-2-0-mini-260428`，用连字符而基础名用点号）。
   因此 `pricing.py` 做了**归一化三级匹配**（精确 → 归一化精确 → 归一化前缀取最长），
   否则新模型一上线就会静默回落兜底价、成本数据悄悄失真。

6. **`text()` 里的 float 参数必须显式 `CAST`。**
   这是实现 `cost-recalc` 时踩到的坑：**asyncpg 会把裸的 `:in_rate` 推断成 integer**，
   于是单价 `0.7` 被静默截断成 `0`、`1.2` 截断成 `1` —— 表现为「单价恰为整数的模型
   算对了，带小数的全错」，而且**不报任何错**。
   首次重算后逐模型抽查才发现：embedding 成本本该 20.40 分却是 0，某模型本该 254.60 却是 185.29。
   修法是 `CAST(:in_rate AS numeric)`。结论：**跨语言/驱动传数值参数时，不要相信类型推断，
   且务必抽查验证，而不是看到"跑通了"就认为对了。**

### 4.3 怎么用

```bash
# 看 Agent 健康面板（成功/降级/失败率、延迟分位、成本、简报质量分布）
uv run python -m fin_news.cli status

# 按某次运行下钻：找出这次 Agent 运行发起的全部 LLM 调用
psql -c "SELECT role, model, latency_ms, prompt_tokens, completion_tokens, status, error_message
         FROM llm_call_log WHERE run_id='<run_id>' ORDER BY id;"

# 按天看趋势
psql -c "SELECT * FROM v_llm_daily ORDER BY day DESC, calls DESC LIMIT 20;"
```

日志里每行都带 `run_id`，可直接与 `agent_run` / `llm_call_log` 互查。

### 4.4 校准单价（**重要**）

`pricing.py` 里的单价是**占位量级，不是真实报价**。请按火山方舟 / DeepSeek 控制台的实际单价配置：

```bash
# .env（单位：元 / 百万 token，区分 input / output）
MODEL_PRICING={"doubao-seed-evolving":{"input":1.0,"output":4.0},"doubao-seed-2.0-mini":{"input":0.3,"output":1.2}}
```

未配置的模型会回落兜底档，并在 `status` 面板底部给出提示（不会静默失真）。

---

## 五、当前已发现的真实问题（新指标上线后的验证靶点）

这些问题是本次改造过程中用新指标/直接查库发现的，**此前完全不可见**。
它们同时充当新监控能力的验证靶点——面板上线后应当能直接看到：

| # | 问题 | 证据 | 对应指标 |
| --- | --- | --- | --- |
| 1 | **`agent_run` 从未被写入** | 改造前线上 0 行，表结构与索引齐全却无业务代码使用 | 全部 Agent 级指标 |
| 2 | **分析链路超时严重** | `analysis` 角色错误率 9.4%、平均延迟 70.1s、**P95 达 361.5s（超过 300s 超时线）** | 失败率、P95 延迟 |
| 3 | **宏观 Agent 实际是坏的** | `macro_policy` 简报 **6 条全部 DEGRADED、0 条 PUBLISHED（降级率 100%）** | 降级率、质量分布 |
| 4 | **个股 / 盘前降级率偏高** | `stock` 33.3%（30/90）、`pre_market` 50%（1/2） | 降级率 |
| 5 | **某模型错误率异常** | `doubao-seed-2-1-pro-260628` 调用 15 次失败 12 次（**80%**），且 P95 361.5s | 错误归因分布 |
| 6 | **成本数据此前完全失真** | 单价按 role 硬编码、不区分模型与 input/output。按真实单价重算后，历史总成本从 **3024.76 分降为 1040.29 分（旧口径高估约 2.9 倍）** | 日成本、单条成本 |

> #6 已在本次改造中修复并全量重算（9 个模型逐模型交叉校验通过）。

> 建议优先处理 #3（宏观 Agent 100% 降级）——这是一个「静默失败」：系统一直在产出报告，
> 但每一份都是降级产物，没有任何指标能发现，直到现在。

---

## 六、后续演进路线（本轮未做）

1. **工具调用指标落库**（补 P1 最大盲区）：在 `StepTraceHandler` 的
   `on_tool_end` / `on_tool_error` 落一张 `agent_tool_call` 表，即可算出
   「哪个工具最慢 / 最爱失败」，不需要改任何业务逻辑。
2. **阈值告警**：P0 指标配阈值，超限时告警（当前只能人肉看 `status`）。
3. **Web 监控面板**：直接复用 `observability/metrics.py` 的查询层与两个视图。
4. **评估集扩展**：当前评估集只覆盖「资讯评分」且靠人工标注，可扩到深度分析
   与简报，引入 LLM-as-judge 自动打分（如事实性、可用性）。
5. **回归测试集**：每次改 prompt / 换模型自动跑一遍与基线对比，防「改 A 坏 B」。
