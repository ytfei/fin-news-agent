# fin-news-v5 架构设计：模块划分、核心流程与异常处理

## 1. 技术栈

| 层 | 选型 | 说明 |
| --- | --- | --- |
| 语言/包管理 | Python 3.12 + `uv` | `uv` 管理虚拟环境与锁定依赖（`pyproject.toml` + `uv.lock`） |
| API 框架 | FastAPI + Pydantic v2 + Uvicorn | 自动生成 OpenAPI，作为唯一对外数据出口 |
| 调度 | APScheduler 3.x（`BackgroundScheduler` + SQLAlchemyJobStore） | 分钟级增量任务、盘前/盘后定时任务；JobStore 落库避免多实例重复触发 |
| Agent 框架 | DeepAgents（`deepagents.graph.create_deep_agent`） | 编译为 LangGraph `CompiledStateGraph`，支持 `tools` / `subagents` / `middleware` / `checkpointer` |
| 模型接入 | OpenAI 兼容 SDK（`openai` / `langchain-openai` ChatOpenAI） | 火山引擎、DeepSeek 通过 `base_url + api_key + model` 切换 |
| ORM / 迁移 | SQLAlchemy 2.0（DeclarativeBase, asyncpg）+ Alembic | 全 async；psycopg3 同步路径仅用于 Alembic |
| 数据库 | PostgreSQL 16 + pgvector | 事务型数据 + 向量检索 + 事件队列（不引入 Kafka/Redis，降低运维复杂度） |
| 向量检索 | pgvector（`vector` 类型 + HNSW 索引 + `<=>` 余弦距离） | 与业务库同库，事务一致，规模到百万 chunk 前无需独立向量库 |
| 前端 | React 18 + TypeScript + Vite + TanStack Query | 本期只做 Web |
| 可观测 | structlog（JSON 日志）+ OpenTelemetry（可选）+ Prometheus exporter（可选） | 每个环节带 `trace_id / news_id / agent_type` |

## 2. 总体架构

```
┌────────────────────────────────────────────────────────────────────────┐
│                        Tushare Pro (HTTP API)                          │
└───────────────┬────────────────────────────────────────────────────────┘
                │  pro.news / pro.major_news / pro.daily / pro.us_daily ...
                ▼
┌────────────────────────────────────────────────────────────────────────┐
│  ingestion/        APScheduler: */1 * * * *                            │
│  ┌──────────┐ ┌──────────┐ ┌────────────┐ ┌────────────┐ ┌──────────┐  │
│  │ fetcher  │→│normalizer│→│  deduper   │→│ rule_filter│→│  repo    │  │
│  └──────────┘ └──────────┘ └────────────┘ └────────────┘ └────┬─────┘  │
│        cursor 增量位点            发出事件 news.ingested        │        │
└───────────────────────────────────────────────────────────────┼────────┘
                                                                ▼
┌───────────────────────────── PostgreSQL ───────────────────────────────┐
│  news_item │ news_chunk(vector) │ analysis_report │ ingest_event       │
│  agent_run │ ingest_cursor │ market_daily │ chat_session/message       │
└───────────────────────────────────────────────────────────────┬────────┘
                        event queue (SELECT FOR UPDATE SKIP LOCKED)
                                                                ▼
┌────────────────────────────────────────────────────────────────────────┐
│  pipeline/  事件驱动编排（worker 池，可多副本）                         │
│                                                                        │
│  news.ingested ──► [scoring_agent] ──► score>3 ? ──┐                   │
│                          │                          │                  │
│                          ▼ news.scored               ▼ embed            │
│                    score<=3 归档为 NOISE       [embedder] ──►           │
│                                                    news.embedded        │
│  news.embedded ──► router ──┬─ score>7  ──► macro_policy_agent (+联网)  │
│                             ├─ (5,7]    ──► industry_agent             │
│                             └─ (3,5]    ──► stock_agent                │
│                                             │                          │
│                             ┌───────────────┴──────────┐               │
│                       analysis_report 落库        news.analyzed 事件    │
└────────────────────────────────────────────────────────────────────────┘
        ▲ cron 07:30 pre_market_agent    ▲ cron 15:30 post_market_agent
        │                                │
┌───────┴────────────────────────────────┴───────────────────────────────┐
│  api/  FastAPI  ──►  web(React/TS)  /  未来 Flutter(iOS)               │
│  /news  /analysis  /market/pre-market|post-market  /search  /chat       │
└────────────────────────────────────────────────────────────────────────┘
```

## 3. 模块结构（目录）

```
fin-news-v5/
├── pyproject.toml                 # uv 管理，依赖与可选 extras
├── alembic/                       # 迁移（含 pgvector 扩展与 HNSW 索引）
├── src/fin_news/
│   ├── core/
│   │   ├── config.py              # pydantic-settings：DB/LLM/调度/限流/预算
│   │   ├── logging.py             # structlog + trace_id
│   │   ├── db.py                  # async engine / sessionmaker / 事务装饰器
│   │   ├── enums.py               # ScoreBand, NewsStatus, AgentType, EventStatus, RunStatus
│   │   └── timeutil.py            # 时区、交易日(trade_cal)、A股/美股时段判定
│   ├── ingestion/
│   │   ├── scheduler.py           # APScheduler 装配 + misfire/coalesce 策略
│   │   ├── sources/
│   │   │   ├── base.py            # Source 协议：fetch(since, until) -> RawItem[]
│   │   │   ├── tushare_client.py  # 限流(token bucket)、重试、错误分类
│   │   │   ├── tushare_news.py    # pro.news(src=...)
│   │   │   ├── tushare_major.py   # pro.major_news(src=...)
│   │   │   └── tushare_market.py  # daily/daily_basic/index_daily/us_daily/top_list...
│   │   ├── normalizer.py          # 统一 NewsItemCreate；字段清洗、编码、时间解析
│   │   ├── deduper.py             # content_hash + simhash 近似去重
│   │   ├── rule_filter.py         # 廉价前置噪声过滤（正则/黑名单/模板句）
│   │   ├── cursor.py              # ingest_cursor 读写（区间重叠、边界保护）
│   │   └── service.py             # 一次增量任务的编排（事务边界）
│   ├── events/
│   │   ├── bus.py                 # publish() / poll_and_lock() / ack() / fail()
│   │   └── types.py               # NewsIngested / NewsScored / NewsEmbedded / AnalysisDone
│   ├── agents/
│   │   ├── llm/
│   │   │   ├── registry.py        # Provider 注册（volcengine / deepseek / openai 兼容）
│   │   │   ├── factory.py         # role -> (primary, fallback) ChatModel
│   │   │   ├── limiter.py         # 全局/Provider 级 QPS + 并发闸门 + 日预算
│   │   │   └── tracer.py          # token/耗时/成本写 llm_call_log
│   │   ├── embeddings.py          # Embedding 客户端（批量、维度校验、失败重试）
│   │   ├── tools/
│   │   │   ├── retrieval.py       # 向量/关键词检索历史资讯（history_search）
│   │   │   ├── market_data.py     # 行情、估值、龙虎榜、财报预告（tushare 取数）
│   │   │   ├── web_search.py      # 外部信息检索（macro agent 专用）
│   │   │   └── storage.py         # 报告落库工具
│   │   ├── prompts/               # 各 Agent 的 system prompt（版本化，含 version 字段）
│   │   ├── scoring_agent.py       # flash 批量评分（结构化输出）
│   │   ├── macro_policy_agent.py  # score>7
│   │   ├── industry_agent.py      # (5,7]
│   │   ├── stock_agent.py         # (3,5]
│   │   ├── pre_market_agent.py    # cron
│   │   ├── post_market_agent.py   # cron
│   │   ├── qa_agent.py            # 追问（RAG + 工具）
│   │   └── base.py                # build_deep_agent(agent_type) 工厂（统一中间件/截断/校验）
│   ├── pipeline/
│   │   ├── worker.py              # 事件消费主循环（批量拉取、并发控制、优雅退出）
│   │   ├── handlers/
│   │   │   ├── on_ingested.py     # 攒批 -> 评分
│   │   │   ├── on_scored.py       # score>3 -> chunk + embedding
│   │   │   └── on_embedded.py     # 按 band 路由 -> 深度分析
│   │   ├── router.py              # score -> AgentType
│   │   └── batching.py            # 时间窗/条数窗攒批
│   ├── domain/                    # 领域模型与纯函数（无 IO，易测）
│   │   ├── scoring.py             # 分数 -> band；阈值校验
│   │   ├── chunking.py            # 文本分块策略
│   │   └── schemas.py             # 内部 DTO
│   ├── repo/                      # SQLAlchemy 仓储层（唯一写库入口）
│   │   ├── news_repo.py  chunk_repo.py  event_repo.py
│   │   ├── analysis_repo.py  market_repo.py  run_repo.py  chat_repo.py
│   ├── api/
│   │   ├── deps.py                # session、分页、鉴权（MVP: 设备 ID）
│   │   ├── errors.py              # 统一错误码与 Problem Details
│   │   └── routers/               # news / analysis / market / search / chat / admin / health
│   └── main.py                    # FastAPI app + lifespan（启动 scheduler & worker）
├── web/                           # React + TS 前端
├── docs/
└── tests/
```

**分层约束（单向依赖）**：`api → repo → domain`；`pipeline → agents / repo / domain`；`agents` 只通过 `tools` 访问数据与库，不直接依赖 `api`；`domain` 不依赖任何上层。

## 4. 核心流程

### 4.1 分钟级增量接入（Ingestion）

```
Scheduler(*/1 * * * *, coalesce=True, misfire_grace_time=120s, max_instances=1)
  └─ IngestionService.run(source_key)
     1. 锁：SELECT ... FOR UPDATE 取 cursor（行锁，防止多实例并发）
     2. since = cursor.last_cursor_time - OVERLAP(默认 5min)；until = now()
     3. 分批调用 Tushare（单源单次 ≤ 1500 条；时间窗切分，避免超时）
     4. normalize → dedup（content_hash 精确 + simhash 近似）→ rule_filter
     5. 事务内 bulk upsert news_item；更新 cursor = max(publish_time)（仅当本轮成功）
     6. 事务提交后 publish(news.ingested, news_id...) —— 事件与数据同库同事务，避免"事件先于数据"
     7. 写 agent_run 运行记录（条数、耗时、成败）
```

### 4.2 评分流程

```
on_ingested(events)
 1. 攒批：等待 window=15s 或 batch_size=30（先到先触发），同一批只处理 status=NEW 的资讯
 2. 原子占位：UPDATE news_item SET status='SCORING' WHERE id IN (...) AND status='NEW'
 3. 构造批量 prompt：编号 + 标题 + 摘要(正文截断 ~600 字) + 发布时间 + 来源
 4. 调 flash 模型，response_format=json_schema，输出
    {"items":[{"id":int,"score":int,"band":"...","reason":string,
               "tags":[...],"entities":[{"code":"","name":"","type":""}],
               "confidence":float}]}
 5. 校验：缺项/越界/幻觉 id → 该条标记 SCORE_FAILED 进入重试；缺失项单条补打（小批量）
 6. 写 news_score 记录 + 更新 news_item.status=SCORED
 7. publish(news.scored)
```

### 4.3 向量化流程

```
on_scored(event)
 score <= 3  → status=ARCHIVED_NOISE，结束（不向量化、不分析）
 score > 3   → chunking（512-800 token / overlap 80）
             → 批量 embedding（批 ≤ 64，维度校验）
             → 事务内 upsert news_chunk（先删后插，保证幂等）
             → news_item.status=EMBEDDED
             → publish(news.embedded)
```

### 4.4 深度分析流程

```
on_embedded(event)
 band = router(score)
   >7        -> macro_policy_agent  （工具：history_search（强制）、web_search（强制）、market_data）
   (5,7]     -> industry_agent      （工具：history_search、market_data、valuation）
   (3,5]     -> stock_agent         （工具：market_data、valuation、moneyflow/lhb）
 1. 幂等键：唯一索引 (news_id, agent_type, prompt_version) —— 命中则跳过
 2. run = agent_run(RUNNING)；build_deep_agent(...) -> agent.ainvoke(state, config)
 3. 结构化输出（response_format）校验 + 引用 id 回填校验（引用的 news_id 必须存在）
 4. 事务：写 analysis_report（status=PUBLISHED）+ news_item.status=ANALYZED
 5. publish(analysis.published)（用于前端通知、后续关联推送）
```

### 4.5 盘前 / 盘后流程

```
cron 07:30（交易日）pre_market_agent
  输入：us_daily 隔夜行情（道指/纳指/标普/费城半导体/中概 + 关键权重股）
        近 12h 高评分资讯（score>=6）与其分析摘要
        昨日 A 股收盘状态（index_daily + 涨跌家数 + 成交额）
  输出：外盘速览 / 隔夜要闻 TOP N / 今日展望（方向 + 关注板块）/ 风险提示

cron 15:30（交易日）post_market_agent
  输入：当日指数、涨跌家数、成交额、板块涨跌幅 TOP/BOTTOM、龙虎榜与资金流
        当日全部 score>3 资讯及其评分序列
  输出：一句话定调（涨/跌/震荡 + 主因）
        归因列表（按贡献度排序，每条挂 news_id 引用）
        主线与轮动、资金面、次日关注
```

### 4.6 追问流程（QA）

```
POST /chat/sessions/{id}/messages（SSE 流式）
 1. 向量检索（近 7 天，top_k=8，可按 score/时间/标的代码过滤）+ 关键词召回并集重排
 2. 组装上下文：用户问题 + 检索片段（带 news_id/时间/来源）+ 行情快照（按需）
 3. qa_agent 生成，输出带 [ref:news_id] 引用；无法回答时明确"当前资料不足以判断"并给出需要跟踪的信号
 4. 落 chat_message（含引用与 token 消耗），返回 SSE
```

## 5. 状态机

### 5.1 `news_item.status`

```
NEW ──► SCORING ──► SCORED ──► EMBEDDING ──► EMBEDDED ──► ANALYZING ──► ANALYZED
 │         │            │           │            │             │
 │         ▼            ▼           ▼            ▼             ▼
 │    SCORE_FAILED  ARCHIVED_NOISE  EMBED_FAILED        ANALYSIS_FAILED
 │         │            (终态)          │                     │
 └─────────┴──────── retry<=N ─────────┴──────── retry<=N ────┘
                          │
                          ▼ retry>N
                        DEAD（终态，进死信表，人工/定时重跑）
```

### 5.2 `ingest_event.status`

`PENDING → PROCESSING → DONE`，异常回退 `PENDING`（可重试，`available_at` 指数退避）或 `FAILED`（超过最大尝试次数）。

### 5.3 `agent_run.status`

`PENDING → RUNNING → SUCCESS / FAILED / TIMEOUT / CANCELLED`；`FAILED` 可重入（幂等键保证不重复产出报告）。

## 6. 模型抽象层

```python
# core/config 中的角色化配置（示意）
llm:
  providers:
    volcengine: {base_url: https://ark.cn-beijing.volces.com/api/v3, api_key: ${ARK_API_KEY}}
    deepseek:   {base_url: https://api.deepseek.com, api_key: ${DEEPSEEK_API_KEY}}
  roles:
    scoring:   {primary: volcengine:doubao-lite-*,   fallback: deepseek:deepseek-chat}
    analysis:  {primary: volcengine:doubao-pro-*,    fallback: deepseek:deepseek-reasoner}
    qa:        {primary: volcengine:doubao-pro-*,    fallback: deepseek:deepseek-chat}
    embedding: {primary: volcengine:doubao-embedding, dim: ${EMBEDDING_DIM}}
```

- 统一走 OpenAI 兼容协议，`ChatOpenAI(base_url=..., api_key=..., model=...)`，切换只改配置不改代码。
- `LLMRouter.chat(role, messages, response_format)`：主模型失败（超时/5xx/限流）→ 自动切 fallback → 仍失败返回 `LLMUnavailable`，由上层决定重试。
- 全局闸门：Provider 级 QPS 令牌桶 + 角色级并发上限 + 日预算（token/金额）软限，超预算降级为"摘要级分析"。

## 7. 异常分支处置

### 7.1 数据源侧

| 异常 | 检测 | 处置 |
| --- | --- | --- |
| Tushare 限流（每分钟/每天调用上限） | 返回码/异常分类 | 令牌桶主动限速；命中限流则指数退避（5s→30s→120s），**cursor 不前进**，本轮标记为 PARTIAL，下轮继续 |
| 网络超时 / 5xx | 异常捕获 | 单源重试 3 次；失败则该源本轮跳过，其他源不受影响 |
| 无接口权限（`news`/`major_news` 需单独开通） | 权限错误码 | 启动自检告警，禁用该源（配置开关），不产生调度噪音 |
| 单次返回超过限量（1500）/ 时间窗过大 | 行数判断 | 自动按小时切分递归拉取，合并后去重 |
| 数据源延迟（新闻晚于查询时间写入） | 重叠窗口 | cursor 回退 `OVERLAP=5min` + 内容哈希去重兜底 |
| 数据缺字段 / 脏数据 | Pydantic 校验 | 单条丢弃记录 `ingest_error`，不影响整批 |

### 7.2 评分侧

| 异常 | 处置 |
| --- | --- |
| 模型超时 / 5xx / 限流 | 同批重试 2 次（退避 2s/8s）；仍失败则**整批降级为小批（10 条）**再试；最终失败写 `SCORE_FAILED`，事件回退 PENDING |
| JSON 解析失败 / schema 不合法 | 触发一次"修复式重问"（附原始输出要求其修正 JSON）；再失败则小批重试 |
| 模型漏评/多评/编号错乱 | 以输入 id 集合做校验：缺失项单独补打（最多 2 轮），多余 id 丢弃 |
| 分数越界（<1 或 >10）或非整数 | clamp + 四舍五入，记录 `score_adjusted`；无法解析按 `SCORE_FAILED` 处理 |
| 批量过大导致截断 | 按 token 预估动态分批（不超过模型上下文 60%） |
| 评分分布异常（某一批全是 10 分） | 熔断校验：批内 ≥80% 同分数时标记 `SUSPECT`，抽样进入人工复核队列，不阻断主流程 |

### 7.3 向量化侧

| 异常 | 处置 |
| --- | --- |
| Embedding 服务失败 | 批内重试 3 次；失败则 chunk 级重试（单条失败不影响整篇其他 chunk） |
| 维度不匹配 | 启动自检 + 写入前校验，直接失败并告警（**禁止写入错误维度**，会污染索引） |
| 单篇超长（>32k token） | 截断策略：保留标题 + 首尾 + 关键段，标记 `truncated=true` |
| 重复入库 | `(news_id, chunk_index)` 唯一索引 + 事务内先删后插，天然幂等 |

### 7.4 分析侧

| 异常 | 处置 |
| --- | --- |
| 分析超时 | 单次 `run_timeout`（默认 300s）中断；`agent_run.status=TIMEOUT`，事件回退重试；重试时降低工具预算并提示"精简" |
| 工具调用失败（行情/检索/联网搜索） | 工具内捕获并返回结构化错误，Agent 可降级继续；联网搜索不可用时 macro agent 输出标记 `external_sources_unavailable` 并降低置信度，**不阻塞** |
| 结构化输出校验失败 | 重试 1 次（附带校验错误）；仍失败则保存"原始文本版报告" `status=DEGRADED`，前端标记"内容质量降级" |
| 引用资讯 id 不存在（幻觉引用） | 校验阶段剔除该引用；引用缺失率 > 50% 判定为低质，重跑一次，重跑仍失败则 `DEGRADED` |
| 并发/预算打满 | 事件回退，加大 `available_at` 延迟；高评分（>7）优先消费（优先级队列） |
| 重复消费 | 幂等唯一键 `(news_id, agent_type, prompt_version)` 命中则直接 ack |

### 7.5 调度与运行时

| 异常 | 处置 |
| --- | --- |
| 上一分钟任务未结束（重叠） | `max_instances=1` + `coalesce=True`，跳过重叠触发并告警 |
| 服务重启导致任务错失 | `misfire_grace_time=120s`；超过宽限期则启动后执行一次"补跑"（从 cursor 追平到 now） |
| 多副本部署重复触发 | APScheduler JobStore 落库 + 唯一 job name 抢锁；事件消费用 `SKIP LOCKED` 保证单消费者 |
| 进程优雅退出 | `SIGTERM` → 停止调度 → worker 完成在途批次（≤30s）→ ack 未确认事件回退 PENDING → 关闭连接池 |
| 非交易日 | `trade_cal` 判定，跳过盘前/盘后任务；资讯接入照常（美股/海外新闻周末仍有价值） |
| 数据库连接中断 | 连接池 `pool_pre_ping` + 事务重试装饰器（3 次）；事件全部留在 PENDING，恢复后自动续跑 |
| 磁盘/连接耗尽 | 事件表分区 + 定期清理 DONE 事件（保留 7 天） |

### 7.6 API 侧

统一错误响应（RFC 9457 Problem Details）：`400 参数错误 / 401 未授权 / 404 不存在 / 429 限流 / 500 内部错误 / 503 依赖不可用（DB 或 LLM）`。追问接口 LLM 不可用时不报错，返回"分析服务暂时不可用，请稍后重试"。

## 8. 并发、限流与性能

- **事件消费**：worker 每次 `SELECT ... WHERE status='PENDING' AND available_at<=now() ORDER BY priority DESC, created_at LIMIT 50 FOR UPDATE SKIP LOCKED`，支持多副本水平扩展。
- **优先级**：宏观(3) > 行业(2) > 个股(1)；盘前/盘后任务优先级最高(5)。
- **并发闸门**：`analysis` 角色并发上限（默认 8）；`scoring` 批量并发上限（默认 4）；Tushare 全局 QPS（默认 200/分钟，按积分调整）。
- **索引**：`news_item(publish_time DESC)`、`news_item(score DESC, publish_time DESC)`、`news_item(status)` 部分索引、`news_chunk` HNSW（`vector_cosine_ops`, `m=16, ef_construction=64`）、`ingest_event(status, available_at)` 部分索引。
- **批量**：DB 写入全部走 `bulk` + `ON CONFLICT DO UPDATE`；Embedding 批量 ≤64。
- **缓存**：API 层对盘前/盘后简报、市场概览做短 TTL（30–60s）缓存。

## 9. 可观测与运维

- 结构化日志字段：`trace_id / news_id / agent_type / source / attempt / latency_ms / model / tokens`。
- 指标：各环节吞吐与耗时 P50/P95、事件积压量（`PENDING` 且 `available_at` 超期）、失败率（按错误类型）、LLM 成本日累计、评分分布直方图。
- 告警：事件积压 > 500 或最老 PENDING 超 15min、单环节失败率 > 5%、LLM 连续失败 5 次、日成本超预算 80%、Tushare 连续 3 轮拉取 0 条。
- 运维入口（`/admin`）：重跑资讯（指定 id/时间区间）、重算评分（指定 prompt_version）、重跑分析、清空死信、查看事件积压。

## 10. 安全与合规

- Tushare token、模型 API Key 走环境变量/密钥管理，不入库、不进日志（脱敏）。
- 所有对外输出附"AI 生成，仅供参考，不构成投资建议"；系统提示中硬性约束：不预测具体点位、不承诺收益、不做买卖指令。
- MVP 鉴权：匿名 `device_id`（Header `X-Device-Id`）；后续接入账号体系时不改 API 契约。
- API 限流：按 IP/device 令牌桶；`/chat` 单会话速率限制。
