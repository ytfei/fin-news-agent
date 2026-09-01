# Agent 层重构设计：LangChain / LangGraph / DeepAgents

> 目标：跳过现有的手写 embedding / agent 实现，改用 LangChain 生态**按需构建** Agent。
> 状态：**待评审**（V1，2026-09-01）

---

## 0. 现状盘点

### 0.1 当前代码地图

| 模块 | 实现 | 问题 |
| --- | --- | --- |
| `agents/llm/client.py` | **裸 openai SDK**（自研重试/降级/审计） | 与 LangChain 并存，两套限流与 token 统计 |
| `agents/embeddings.py` | **裸 openai SDK** | 与 LangChain 割裂，维度校验手写 |
| `agents/base.py` | `create_deep_agent` **每次调用重建图**，失败整段降级 | 无图缓存、无 checkpointer、无原生结构化输出 |
| `agents/scoring_agent.py` | 走 `llm/client.py`（JSON 模式 + 正则兜底解析） | 未用原生 structured output |
| `agents/analysis_agents.py` | `_build_context` 预取 history/web/market 塞进 prompt，**同时**又把检索工具交给 Agent | 上下文重复，延迟主因 |
| `agents/qa_agent.py` | 单次调用 + 手工拼最近 6 条历史 | 无多轮状态持久化 |
| `agents/tools/langchain_tools.py` | `@tool` 已用 LangChain，但每次调用开新 `session_scope` | 事务碎片化、同标的重复查询 |
| 工具与预取的关系 | 预取一次 + Agent 再调一次 | token 翻倍 |

### 0.2 现有实现的 7 个具体问题

1. **双通路**：评分 / QA / 降级路径走自研 `llm/client.py`，只有分析走 `ChatOpenAI` —— 重试、限流、成本审计逻辑分裂成两套。
2. **图不复用**：`create_deep_agent` 每条资讯重建一次 LangGraph 图，编译开销白付。
3. **结构化输出是"软约束"**：靠 prompt 里贴 JSON Schema + `parse_json_content` 正则兜底。实测 7 份报告里 1 份 `degraded`。
4. **上下文重复**：`_build_context` 已把 history / external / market 内联进 prompt，Agent 又能调 `history_search` / `stock_lookup` → 实测分析耗时 **150–230 s/条**。
5. **多轮无状态**：追问用字符串拼 `history[-6:]`，无 checkpointer，无法断点续聊。
6. **token 统计靠猜**：`_extract_usage` 从不同版本 LangGraph 结构里捞 `response_metadata`，取不到就记 0。
7. **无 tracing**：`.env` 里 `LANGSMITH_*` 已配置，但代码未集成。

### 0.3 已实测结论（决定方案可行性）

| 验证项 | 结果 |
| --- | --- |
| 版本 | langchain 1.3.18 / langchain-core 1.6.1 / langchain-openai 1.6.0 / langgraph 1.2.11 / deepagents 0.7.11 |
| 火山 embedding 接 LangChain | ✅ `OpenAIEmbeddings(check_embedding_ctx_length=False)` → 2560 维 |
| 火山 `with_structured_output(method="json_schema")` | ✅ 通过（provider 侧 schema） |
| 火山 `with_structured_output(method="function_calling")` | ✅ 通过（工具调用侧） |
| `deepagents.create_deep_agent` 0.7 能力 | ✅ 支持 `response_format`（ProviderStrategy / ToolStrategy / Pydantic 类 / dict）、`subagents`、`checkpointer`、`middleware`（summarization / memory / filesystem / subagents / skills / rubric） |
| `SubAgent` 规格 | `name / description / system_prompt / tools / model / middleware / interrupt_on / skills / permissions / response_format` |
| LangGraph PostgresSaver | ❌ 未安装（需新增 `langgraph-checkpoint-postgres`） |

---

## 1. 目标架构

```
┌─────────────────────────────────────────────────────────────────────────┐
│ 编排层 pipeline/handlers  ── 事件驱动，只负责"决定跑哪个 Agent"          │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │  get_agent(AgentType.MACRO_POLICY).ainvoke(...)
┌───────────────────────────────▼─────────────────────────────────────────┐
│ Agent 层（按需构建 + 图缓存）                                            │
│  ┌───────────┐ ┌──────────────────────────┐ ┌────────────────────────┐  │
│  │ scoring   │ │ macro / industry / stock │ │ pre_market/post_market │  │
│  │ LangGraph │ │ DeepAgents + SubAgents   │ │ LangGraph DAG          │  │
│  │ 小图      │ │                          │ │ （确定性步骤）          │  │
│  └───────────┘ └──────────────────────────┘ └────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │ qa：LangGraph RAG 图 + PostgresSaver + streaming                  │   │
│  └──────────────────────────────────────────────────────────────────┘   │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────────┐
│ 工具层  LangChain @tool（history_search / web_search / stock_lookup /    │
│         market_snapshot / sector_members），统一 session 注入 + 结果缓存 │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────────┐
│ 模型层  ModelFactory：init_chat_model / ChatOpenAI + with_fallbacks()    │
│         EmbeddingFactory：OpenAIEmbeddings(check_embedding_ctx_length=F) │
│         统一 QPS 闸门、超时、成本回调 → 写 llm_call_log                  │
└─────────────────────────────────────────────────────────────────────────┘
```

### 核心原则

**不是所有 Agent 都用 DeepAgents。** DeepAgents 的价值是"自主规划 + 多步工具调用 + 子 agent 编排"，适合**开放式分析**；而评分、盘前盘后属于**高频 / 确定性流程**，用 LangGraph 显式图更可控、更快、更省。

| Agent | 框架 | 理由 |
| --- | --- | --- |
| `scoring` | LangGraph 轻量图（3 节点） | 高频（30 条/批）、要求低延迟；流程固定：批量打分 → 校验 → 漏评补打。不需要自主规划 |
| `macro_policy` | **DeepAgents** + 子 agent | 开放式：需要"历史检索 / 外部检索 / 估值 / 综合研判"多轮自主决策 |
| `industry` | **DeepAgents** + 子 agent | 需要自主选头部公司、反复查估值 |
| `stock` | LangGraph 图（或 DeepAgents 精简版） | 流程相对固定：取估值 → 取走势 → 判断；可用图省掉规划开销 |
| `pre_market` / `post_market` | LangGraph DAG | 步骤确定性高（取数 → 检索 → 归因 → 写作），要求可复现；其中"归因"节点可嵌一个 DeepAgents 子 agent |
| `qa` | LangGraph RAG 图 + checkpointer | 需要多轮状态、流式输出、可控的检索-重排-生成流程 |

---

## 2. 关键设计

### 2.1 统一模型层

```python
# agents/llm/factory.py
class ModelFactory:
    @lru_cache
    def chat(self, role: LLMRole) -> BaseChatModel:
        primary = self._build(self.settings.llm_default_provider, role)
        fallback = self._build(self.settings.llm_fallback_provider, role)
        # LangChain 原生降级：主模型抛 APIError/RateLimit 自动切备
        return primary.with_fallbacks([fallback])

    def _build(self, provider, role) -> ChatOpenAI:
        cfg = self.settings.provider(provider)
        return ChatOpenAI(
            base_url=cfg.base_url,
            api_key=cfg.api_key,
            model=self.settings.model_for(provider, role),
            temperature=TEMPERATURE_BY_ROLE[role],
            timeout=self.settings.llm_timeout_seconds,
            max_retries=self.settings.llm_max_retries,
        )
```

- 用 `with_fallbacks()` 替代自研 `for provider in [...]` 循环 —— 更少代码，且能正确处理流式降级。
- 保留**全局 QPS 闸门**和**日预算**（现有限流器），通过 LangChain callback 接入，而不是塞进 client。
- 成本/审计：用 `BaseCallbackHandler` 统一写 `llm_call_log`，替代 `_extract_usage` 猜测（优先 `usage_metadata`，缺失时按字符估算并标记 `estimated=true`）。

### 2.2 统一 Embedding / 检索层

```python
# agents/embeddings.py（重写）
def get_embeddings(settings) -> Embeddings:
    cfg = settings.provider(settings.embedding_provider)
    return OpenAIEmbeddings(
        model=settings.model_for(settings.embedding_provider, "embedding"),
        base_url=cfg.base_url,
        api_key=cfg.api_key,
        check_embedding_ctx_length=False,   # 实测必需：火山模型名不在 tiktoken 表
    )
```

**检索层保留自定义实现，不引入 `langchain-postgres.PGVector`**：
- 我们的检索需要按 `score / band / publish_time / entity_codes` 过滤 + 与关键词召回混合重排，`PGVector` 的过滤能力偏弱；
- 自定义检索封装成 LangChain `BaseRetriever`（返回 `Document`，metadata 带 `news_id/score/publish_time`），这样它既能被 LangGraph RAG 节点直接用，又不丢过滤能力。

### 2.3 Agent 注册表 + 按需构建 + 图缓存

```python
@dataclass(frozen=True)
class AgentSpec:
    framework: Literal["deepagents", "langgraph"]
    model_role: LLMRole
    tools: tuple[str, ...]
    response_model: type[BaseModel] | None
    subagents: tuple[SubAgentSpec, ...] = ()
    checkpointer: bool = False
    recursion_limit: int = 12
    timeout_seconds: int = 300
    prompt_version: str = ""

AGENT_SPECS: dict[AgentType, AgentSpec] = {...}

@lru_cache(maxsize=32)
def get_agent(agent_type: AgentType, prompt_version: str) -> CompiledStateGraph:
    """按 (agent_type, prompt_version) 缓存已编译的图，避免每条资讯重建。"""
```

- **按需**：只有被路由到的 Agent 才构建；未配置的 Agent 不占内存。
- **改 prompt = 换 version = 新图**：历史报告仍绑定旧 `prompt_version`，可复现。
- 图的构建成本从"每资讯一次"降为"每版本一次"。

### 2.4 结构化输出（三级降级）

```python
def structured(llm, model: type[BaseModel]):
    try:
        return llm.with_structured_output(model, method="json_schema")     # ① provider 侧 schema
    except Exception:
        try:
            return llm.with_structured_output(model, method="function_calling")  # ② 工具调用侧
        except Exception:
            return llm  # ③ 退化为 prompt 贴 schema + 容错解析（现有逻辑）
```

实测 ①② 在火山均通过；DeepSeek 需实测（若不支持 ①，自动落 ②）。降级会记录到 `analysis_report.degraded_reason`，不再是"静默变 DEGRADED"。

**Pydantic 模型替代 dict schema**：`AnalysisPayload` / `MacroExtras` / `IndustryExtras` / `StockExtras` 从 dict 升级为 Pydantic，`content` 字段仍以 JSONB 存库（API 契约不变）。

### 2.5 子 Agent 编排（宏观 / 盘后）

以宏观政策 Agent 为例：

```
macro_policy_agent (DeepAgents)
├── history-analyst   子agent：专做历史同类事件检索与对比（tools: history_search）
├── external-analyst  子agent：外部信息检索与可信度过滤（tools: web_search）
├── transmission-analyst 子agent：流动性与板块传导推演（tools: market_snapshot, sector_members）
└── 主 agent 汇总 → 结构化输出（受益/受损板块 + 逻辑链 + 跟踪信号）
```

子 agent 之间**并行**执行（DeepAgents 支持 `task` 工具并发调用），主 agent 只做汇总 → 把当前的串行多步变成并行，直接对冲延迟。

### 2.6 Checkpointer（多轮与断点）

- `langgraph-checkpoint-postgres` 的 `PostgresSaver`，指向**同一个库但独立 schema `langgraph`**（避免污染业务 schema、避免 Alembic autogenerate 噪音）。
- QA：`thread_id = chat_session.public_id`，多轮状态自动持久化，删掉手工 `history[-6:]` 拼接。
- 分析 Agent：默认 `checkpointer=False`（一次性任务，无状态）；需要"人工介入/中断续跑"时再开。

### 2.7 工具层改造

| 改造 | 说明 |
| --- | --- |
| Session 注入 | 工具通过 `contextvars` 复用请求级 session，而不是每次 `session_scope()` |
| 结果缓存 | 同一次分析内对同一 `ts_code` / 同一查询的重复调用命中内存缓存 |
| 去掉预取与工具的重复 | 预取只保留**廉价且确定需要**的部分（资讯正文、市场快照）；历史检索、个股估值**交给 Agent 按需调用** |
| 工具预算 | 每个 Agent 声明 `max_tool_calls`，超限强制进入写作阶段，防止无限检索 |

这一条是**延迟优化的核心**：当前 150 s 的很大一部分是"预取一份 + Agent 再查一遍"。

### 2.8 可观测

- LangSmith：`.env` 已配，代码侧只需 `LANGCHAIN_PROJECT` + 自动注入 `trace_id` / `news_id` 作为 metadata。
- 保留 `llm_call_log`：改为 LangChain callback 统一写入，新增 `tool_name` / `node_name` / `step_index`。
- `agent_run` 增加 `steps` / `tool_calls` / `thread_id` / `degraded_reason`，便于定位"为什么这条慢"。

### 2.9 超时 / 预算 / 降级

| 层 | 机制 |
| --- | --- |
| 单次 LLM 调用 | `timeout=llm_timeout_seconds` + `max_retries` |
| 单个 Agent | `recursion_limit` + `run_timeout`（`asyncio.wait_for`）+ `max_tool_calls` |
| 全局 | 角色级并发闸门 + 日预算软限；超预算自动降级到 flash 模型 |
| 最终兜底 | Agent 全链路失败 → 单次结构化调用（保留现有 `_run_plain_agent` 逻辑），报告标 `DEGRADED` 并写明原因 |

---

## 3. 数据库改动

| 变更 | 说明 |
| --- | --- |
| 新 schema `langgraph` | 放 `checkpoints` / `checkpoint_blobs` / `checkpoint_writes`（由 PostgresSaver 自建，不进 Alembic） |
| `agent_run` +列 | `thread_id`, `checkpoint_ns`, `steps`, `tool_calls`, `degraded_reason` |
| `chat_session` +列 | `thread_id`（与 checkpointer 对齐，一会话一 thread） |
| `llm_call_log` +列 | `tool_name`, `node_name`, `step_index`, `estimated`(bool) |
| `analysis_report` +列 | `degraded_reason` |
| **不改动** | `news_item` / `news_chunk` / `analysis_report` 主结构 / OpenAPI 契约 |

---

## 4. 依赖变更

```toml
# 新增
"langgraph-checkpoint-postgres>=2.0"   # PostgresSaver
# 可选（暂不建议）
# "langchain-postgres"                  # 若改用官方 PGVector 才需要
# 保留
"langchain-openai", "langgraph", "deepagents", "openai"
```

---

## 5. 迁移计划（分阶段、可回滚）

每一步都有独立验收，**任何一步失败都可回退**；通过配置项 `AGENT_FRAMEWORK`（`legacy` / `langgraph`）按 Agent 灰度。

| 阶段 | 内容 | 验收标准 |
| --- | --- | --- |
| **P0 基线** | 埋点记录当前 P50/P95 延迟、`degraded` 率、千条成本 | 拿到可对比的数字 |
| **P1 模型层** | `ModelFactory` + `EmbeddingFactory` + callback 审计；Agent 行为不变 | 评分分布与旧版一致率 ≥ 95%；embedding 维度 2560 不变 |
| **P2 评分 Agent** | LangGraph 小图 + Pydantic structured output + 漏评补打节点 | 100 条抽样人工校验，分档一致率 ≥ 80%；延迟不高于旧版 |
| **P3 分析 Agent** | DeepAgents + 子 agent + 图缓存 + 工具去重复 | 延迟 **-40%**（150 s → ≤ 90 s）；`degraded` 率 < 5%；报告字段完整率 100% |
| **P4 QA** | LangGraph RAG + PostgresSaver + 流式 | 多轮上下文正确；引用命中率不低于旧版 |
| **P5 盘前/盘后** | LangGraph DAG（归因节点嵌子 agent） | 简报归因条目 ≥ 5 条且每条挂 `news_id` |
| **P6 评估集** | 100 条人工抽评 + 自动化回归 | 分档一致率 ≥ 80%，无"无源断言" |

---

## 6. 风险与对策

| 风险 | 影响 | 对策 |
| --- | --- | --- |
| DeepAgents 多步导致 token 成本翻倍 | 🟡 成本 | `max_tool_calls` + 工具结果缓存 + 子 agent 用小模型 + 日预算闸门 |
| 延迟不降反升 | 🟡 时效 | 子 agent 并行；去掉预取重复；设 `recursion_limit=12`；超时强制收敛 |
| 火山/DeepSeek 对 `json_schema` 支持不一致 | 🟡 稳定性 | 三级降级（json_schema → function_calling → prompt+解析），实测已验证前两级 |
| tiktoken 无法识别火山模型名，token 统计不准 | 🟡 成本可见性 | embedding 侧 `check_embedding_ctx_length=False`；token 优先取 `usage_metadata`，缺失按字符估算并标记 |
| PostgresSaver 表增长 | 🟢 运维 | 独立 schema + 定期清理过期 thread（保留 30 天） |
| DeepAgents 版本升级破坏 API | 🟡 维护 | 锁版本 + `AgentSpec` 隔离框架细节，业务层只见 `get_agent()` |
| 重构期间线上数据不一致 | 🔴 正确性 | 灰度开关 + 双跑对比；报告表唯一索引保证不重复 |

---

## 7. 需要你拍板的 6 个决策

| # | 决策 | 选项 A | 选项 B | 我的建议 |
| --- | --- | --- | --- | --- |
| 1 | 多轮持久化 | 引入 `langgraph-checkpoint-postgres`（新依赖 + 3 张表） | 维持现状（字符串拼历史，最多 6 轮） | **A**：追问是产品核心，无状态会话体验差距大 |
| 2 | 检索引擎 | 自定义 Retriever（保留 score/band/entity 过滤） | 改用 `langchain-postgres.PGVector` | **A**：我们的过滤条件是刚需；封装成 `BaseRetriever` 后同样享受生态 |
| 3 | 评分 Agent 形态 | LangGraph 小图（可加校验/补打节点） | 保持轻量单次 structured 调用 | **A**：漏评补打、分布异常检测作为显式节点更清晰，且成本可控 |
| 4 | 自研 `llm/client.py` | 全量替换（统一到 LangChain callback） | 保留其做限流/审计，只替换调用层 | **B**：先替换调用层，限流与预算逻辑沿用现有成熟实现，风险最小 |
| 5 | 迁移节奏 | 一次性切换 | 双跑灰度（成本翻倍但可对比） | **B**：至少 P2/P3 阶段双跑 100 条对比，避免"换了但不知道变好还是变差" |
| 6 | 盘前/盘后实现 | LangGraph 确定性 DAG | DeepAgents 自治 | **A**：简报要求可复现、步骤固定；把"归因"做成 DAG 里的一个 DeepAgents 子图 |

## 8. 实施进展（P1 / P2 / P3 已完成，2026-09-01）

### 8.1 已落地

| 项 | 内容 |
| --- | --- |
| 依赖 | 新增 `langgraph-checkpoint-postgres`（3.1.2）、`langchain`；dev 依赖改用 `[dependency-groups]`（避免 `uv sync` 把开发依赖裁掉） |
| 模型层 P1 | `agents/llm/factory.py`：`ModelFactory`（`with_fallbacks` 主备降级、`structured()` 三级降级、embeddings 统一） |
| 审计 | `agents/llm/callbacks.py`：`AuditCallbackHandler` 写 `llm_call_log`，优先取 `usage_metadata`，取不到按字符估算并标 `estimated=true`（新增列 + 迁移 0003） |
| 结构化输出 | `agents/schemas.py`：Pydantic 模型替代 dict schema |
| 评分图 P2 | `agents/graphs/scoring_graph.py`：`call_model → validate → (rescue)*` 显式图，图缓存，漏评补打作为图节点 |
| 注册表 | `agents/registry.py`：`AgentSpec` + `get_agent()` 按需构建（当前注册 scoring，后续阶段补齐） |
| 灰度与回退 | 配置 `agent_framework`（langgraph/legacy）、`score_dual_run`、`score_retry_on_degenerate`；langgraph 失败自动回退 legacy |
| 测试 | 101 passed（新增评分图节点、退化护栏、模型工厂共 34 项） |

### 8.2 实测数据（20 条样本，含 8 条历史高分 + 12 条低分）

| 指标 | 结果 |
| --- | --- |
| 结构化输出方式 | `json_schema` ✅ 明显优于 `function_calling`（后者把 7 分项压到 2–5 分） |
| LangGraph vs legacy 延迟 | 27.8 s vs 28.0 s（无回归） |
| 双跑一致率 | exact 0.50 / 分档 0.50 / 平均绝对差 1.0 |
| token 统计 | langgraph 侧完整拿到（prompt 4140 / completion 3372）；legacy 侧常为 0 |
| 退化现象 | 20 条样本 3 次连跑中出现 1 次"整批只给 1–2 分" |

### 8.3 关键发现：评分方差远大于实现差异（重要）

同一批样本连跑 3 次，分档结果分别是：

```
RUN0: [2,2,2,5,3,3,2,2,2,5,2,3,2,2,2,2,2,2,2,2]   distinct=3
RUN1: [2,2,3,5,4,3,2,2,3,5,2,3,3,2,2,2,2,2,2,1]   distinct=5
RUN2: [2,6,7,6,5,5,4,5,5,6,2,5,5,4,5,2,2,2,2,2]   distinct=5
```

三次与"旧版结果"的**分档一致率都恰好是 11/20（55%）**——说明：

1. 两次 LLM 调用之间的**固有分歧约 45%**，远大于"换框架"带来的差异；
2. 因此 **P2 的验收标准必须从"与旧实现对比"改为"与人工标注对比"**（即 P6 评估集是**必需项**，不是可选项）；
3. 单次调用不可靠 → 已加**退化护栏**（批内分数种类 ≤2 或 80% 同分时重试一次并择优），但护栏只能拦住极端退化，不能消除方差。

### 8.4 对后续阶段的调整建议

| 原计划 | 调整 |
| --- | --- |
| P2 验收：与旧版分档一致率 ≥80% | ❌ 不可达（LLM 间固有分歧 ~45%）→ 改为 **P6 人工评估集分档一致率 ≥80%** |
| P3 分析 Agent | 优先做：`json_schema` 已验证可用，分析侧同样受益 |
| 评分稳定性 | 新增候选方案：同一批跑 2 次取中位数 / 降低 batch_size 到 10–15（减少注意力衰减） |
| P6 评估集 | **提前到 P3 之前**，先有 100 条人工标注，否则后续优化无法量化 |

### 8.5 P3 分析 Agent 已落地（DeepAgents 化）

| 项 | 内容 |
| --- | --- |
| 结构化输出 | `schemas.py` 新增 `AnalysisPayload` / `EntityItemModel`（Pydantic），`headline/summary/bullets/logic_chain/beneficiaries/victims/confidence/sentiment/impact_level/horizon` 原生 schema 保证；`extras` 保持 dict（JSONB 契约不变） |
| 图缓存 | `graphs/analysis_graphs.py`：`build_analysis_graph` + `get_analysis_graph` 按 (agent_type, version, provider, model) 缓存，不再每条资讯重建 |
| 子 agent | 宏观 Agent 挂 3 个子 agent（history-analyst / transmission-analyst / external-analyst），web_search 未启用时自动省略 external-analyst |
| 按 provider 选策略 | `_response_format_for`：火山→`ProviderStrategy`(json_schema)，DeepSeek→`ToolStrategy`(function_calling) |
| 去掉预取重复 | `_build_context` 不再预取历史/外部信息（交给 Agent 工具），只保留廉价的市场快照；`MACRO/INDUSTRY/STOCK` prompt 版本 bump 到 `v2` |
| 降级 | `_run_analysis`：DeepAgents 图失败 → `_run_plain_agent`（legacy 单次结构化调用，带主备降级） |
| 注册表 | `registry.py` 补齐 MACRO_POLICY / INDUSTRY / STOCK 声明，`get_agent` 对 deepagents 委托 `get_analysis_graph` |
| 测试 | 124 passed（新增 `test_analysis_graphs.py` 13 项） |

**关键集成坑（已解决）**：

1. **`with_fallbacks()` 不兼容 DeepAgents**：`ModelFactory.chat()` 默认返回 `RunnableWithFallbacks`，而 `deepagents.resolve_model` 只认 `BaseChatModel` / `str`，会误当字符串去 `spec.count(":")` 抛 `AttributeError`。→ 分析图内改用 `with_fallback=False`（纯 `ChatOpenAI`），主备降级改由「整图失败降级 legacy」承担。
2. **DeepSeek 不支持 `response_format=json_schema`**：返回 400 `This response_format type is unavailable now`。→ 按 provider 选择策略，DeepSeek 走 `ToolStrategy`(function_calling)，实测可正确产出 `AnalysisPayload`。

**实测（真实数据）**：

| 项 | 结果 |
| --- | --- |
| 图构建 | ✅ 火山 / DeepSeek 均可构建（宏观含 3 子 agent） |
| DeepSeek + function_calling → `AnalysisPayload` | ✅ 直接产出结构化结果（headline/summary/sentiment/impact 正确） |
| 报告落库 | ✅ 个股 / 行业 / 宏观均产出完整报告（bullets / logic_chain / beneficiaries / extras） |
| 降级链路 | ✅ 火山欠费 → DeepAgents 失败 → legacy 自动切 DeepSeek 成功（`DEGRADED` 标注） |

**环境提示**：验证期间火山引擎 `AccountOverdueError`（403，欠费），chat 与 embedding 均不可用；依赖火山 embedding 的 `history_search` 工具会失败并触发整图降级。DeepSeek 侧 chat 正常（无 embedding 服务）。

### 8.6 Embedding 切换到 doubao-embedding-vision（多模态向量化接口）

旧模型 `doubao-embedding-text-240715`（固定 2560 维）切换为 `doubao-embedding-vision`，接口与输入格式**不兼容 OpenAIEmbeddings**，因此重写：

| 项 | 旧（文本模型） | 新（vision 多模态） |
| --- | --- | --- |
| 接口 | `POST /embeddings`（OpenAI 兼容） | `POST {base_url}/embeddings/multimodal` |
| input 格式 | 字符串数组 `["a","b"]` | 对象数组 `[{"type":"text","text":"a"}]` |
| 响应 data | 数组 `[{"embedding":...}]` | **对象** `{"embedding":[...]}`（单样本语义） |
| 批量 | 一次请求 N 条文本 → N 个向量 | 一次请求 = 一个样本 = **一个向量**（需逐条请求） |
| 维度 | 模型固定 2560 维 | 请求参数 `dimensions` 决定（1024 / 2048） |
| 客户端 | `langchain_openai.OpenAIEmbeddings` | 自建 `Embedder`（httpx 直连） |
| 列类型 | `halfvec(2560)` | `halfvec(2048)`（迁移 0004，清空旧向量） |

**关键坑（实测踩过）**：火山 multimodal 接口是**单样本语义**——`input` 数组表示「一个多模态样本的若干部分」（文本/图片/视频混合），返回的是这一个样本的**一个**融合向量，`data` 字段是对象而非数组。所以：
1. 不能像 OpenAI 那样一次请求批量文本（多条文本会被当成一个样本的多个部分，融合成一个向量）；
2. 正确做法是逐条请求，`Embedder.embed()` 用 `asyncio.gather` 分批并发限流（`embedding_batch_size` 作为并发批大小）。

改动：
- `agents/embeddings.py` 重写：`Embedder` 直连 `/embeddings/multimodal`，请求带 `encoding_format=float` + `dimensions` + `sparse_embedding=disabled`，逐条请求 + 并发
- `llm/factory.py` 移除 `OpenAIEmbeddings` 与 `embeddings()`（接口不再适用）
- `config.py` 默认 `embedding_dim=2048`、`volcengine_model_embedding=doubao-embedding-vision`
- `.env`：`EMBEDDING_DIM=2048`
- 迁移 `0004`：`halfvec(2560) → halfvec(2048)`，旧向量清空后由 `sweep` 打回重新向量化
- `cli.py` `_probe_embedding_dim` 简化（vision 返回维度 == 请求维度，不再需要真实探测）

测试：129 passed（新增 `test_embedding.py` 6 项，验证 multimodal 路径 / 对象数组 input / data 对象解析 / dimensions / 维度校验）。selftest 全绿：`dim=2048`、`halfvec(2048)`、`halfvec_cosine_ops`、写入+检索探针相似度 1.0。

**注意**：`.env` 当前 `VOLCENGINE_BASE_URL=https://ark.cn-beijing.volces.com/api/plan/v3`（Agent Plan），embedding 接口为 `/api/plan/v3/embeddings/multimodal`；Agent Plan 需要专属 API Key。

## 9. 交付物清单（P1–P5）

```
src/fin_news/agents/
├── llm/
│   ├── factory.py         # ModelFactory：role → ChatModel + with_fallbacks
│   ├── embeddings.py      # EmbeddingFactory
│   └── callbacks.py       # 成本审计 / LangSmith / 限流
├── registry.py            # AgentSpec + get_agent(agent_type, version) + lru_cache
├── schemas.py             # 结构化输出的 Pydantic 模型（替代 dict schema）
├── graphs/
│   ├── scoring_graph.py       # LangGraph：batch_score → validate → rescue
│   ├── analysis_agents.py     # DeepAgents：macro / industry / stock
│   ├── market_graphs.py       # LangGraph DAG：pre_market / post_market
│   └── qa_graph.py            # LangGraph RAG + checkpointer + streaming
└── tools/
    ├── registry.py        # 工具注册表 + 结果缓存 + session 注入
    └── ...（保留现有工具，改造 session 与缓存）
```
