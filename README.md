# fin-news-v5

财经资讯分析 Agent：及时解释「市场为什么涨跌 + 接下来看什么」，并支持用户持续追问。

技术栈：`Python 3.13 / uv / FastAPI / SQLAlchemy 2.0 / PostgreSQL 16 + pgvector / APScheduler / DeepAgents`
模型：火山引擎 Doubao 与 DeepSeek 通过 OpenAI 兼容接口自由切换。

设计文档见 [`docs/`](./docs/README.md)。

---

## 零、当前状态（本机已验证）

| 项 | 状态 |
| --- | --- |
| Docker 数据库（pgvector/pg16，端口 5433） | 已启动并 healthy |
| `alembic upgrade head` | 已建 25 张表；`autogenerate` 差异为空（模型与库一致） |
| 数据源自检 `cli selftest` | `cls` / `wallstreetcn` 均 OK |
| 增量接入 `cli ingest` | 首次 119 条入库；重复运行正确判重（duplicates=121/inserted=18） |
| 事件链路 `cli pipeline` | 攒批 30 条/批；缺模型 Key 时跳过并把事件放回队列，不进死信 |
| REST API | `/health`、`/news`、`/news/{id}`、`/analysis`、`/market/*`、`/chat/sessions`、`/admin/*` 均正常 |
| 单元测试 `pytest` | 129 passed |
| 静态检查 `ruff` | All checks passed |
| **评分** | 已实跑：火山 `doubao-seed-2-0-mini`；30 条/批拆 3×10 子批并发（`SCORING_SUB_BATCH_SIZE` × `SCORING_CONCURRENCY`），整批约 12–45s |
| **向量化** | 已实跑：`doubao-embedding-vision`（多模态接口 `/embeddings/multimodal`）；一批资讯的全部 chunk 汇入 `EMBEDDING_CONCURRENCY` 闸门并发逐条请求，审计日志攒批落库 |
| **深度分析** | 已实跑：个股 / 行业 Agent 产出结构化报告（含受益板块、龙头、估值、逻辑链） |
| **语义检索** | 已验证：同事件跨源相似度 0.92，无关资讯 0.70 |

```bash
uv run python -m fin_news.cli selftest   # 一条命令验收数据源 / LLM / Embedding / 向量列
```

### Agent 层（LangChain / LangGraph / DeepAgents）

| 阶段 | 状态 | 说明 |
| --- | --- | --- |
| P1 模型层 | ✅ 完成 | `ModelFactory`（`with_fallbacks` 主备降级）+ 结构化输出 + 审计回调 |
| P2 评分 Agent | ✅ 完成 | LangGraph 显式图（打分→校验→漏评补打）+ 图缓存 + 退化护栏；`agent_framework` 可切回 legacy |
| P3 分析 Agent | ✅ 完成 | DeepAgents + 子 agent（宏观：历史/传导/外部并行）+ 图缓存 + Pydantic 结构化输出；prompt 版本 `v2` |
| P4 追问 | ⏳ 待做 | LangGraph RAG + PostgresSaver 多轮 |
| P5 盘前/盘后 | ⏳ 待做 | LangGraph DAG |
| P6 评估集 | ⏳ **建议提前** | 实测两次 LLM 调用分档分歧约 45%，必须有人工标注才能量化优化 |

设计文档见 `docs/05-agent-refactor-design.md`（含实测数据与发现）。

> 未配置模型 Key 时，追问接口返回 `503 + "分析服务暂时不可用"`，评分/分析事件会放回队列等待，不会进死信。

### 已知缺口

- **行情/估值数据未接入**：`stock_daily` / `daily_basic` / `us_daily` 等表为空，
  行业 Agent 输出里的 `pe_ttm` / `pb` / 分位数目前是 `null`；盘前盘后 Agent 也只能做定性分析。
  这是下一步要补的「市场数据同步任务」。

---

## 一、快速开始

### 1. 启动数据库（Docker）

```bash
docker compose up -d          # pgvector/pgvector:pg16，宿主机端口 5433
docker compose ps             # 确认 STATUS = healthy
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 必填：TUSHARE_TOKEN
# 分析链路必填（二选一或都填）：VOLCENGINE_API_KEY / DEEPSEEK_API_KEY
```

### 3. 安装依赖

```bash
uv sync
```

### 4. 建库（Alembic 迁移）

```bash
uv run alembic upgrade head
```

### 5. 自检数据源连通性

```bash
uv run python -m fin_news.cli selftest
```

预期输出（当前配置的是 `cls` + `wallstreetcn`）：

```
数据源：['cls', 'wallstreetcn']
LLM 凭据：未配置（分析链路将跳过）
  cls: OK, 近 3 小时 8 条
  wallstreetcn: OK, 近 3 小时 43 条
```

### 6. 跑一次增量接入

```bash
uv run python -m fin_news.cli ingest     # 拉取 + 归一化 + 去重 + 落库 + 发事件
uv run python -m fin_news.cli status     # 查看位点与统计
```

### 6b. 手动补数与状态修正

事件驱动链路之外，有三个直接入口：

```bash
uv run python -m fin_news.cli score          # 只评分（评分后会自动补发 news.scored）
uv run python -m fin_news.cli embed          # 只向量化（status=SCORED/EMBED_FAILED 且 score>3）
uv run python -m fin_news.cli sweep          # 体检：噪声未归档 / 缺评分事件 / 分块缺失
uv run python -m fin_news.cli sweep --apply  # 实际修正
```

`sweep` 的三类问题：

| 现象 | 原因 | 修正 |
| --- | --- | --- |
| `status=SCORED` 但 `score<=3` | 评分发生在事件流之外 | 归档为 `ARCHIVED_NOISE` |
| `status=SCORED` 且 `score>3` 但没有 `news.scored` 事件 | 同上 | 补发事件，进入向量化 |
| `status=EMBEDDED` 但没有分块 | 向量化写入失败 | 打回 `SCORED` 并补发事件重跑 |

### 7. 启动服务（接入调度 + Pipeline + API）

```bash
uv run python -m fin_news.main
# 或：uv run uvicorn fin_news.api.app:app --reload
```

- API 文档：http://localhost:8000/docs
- 健康检查：http://localhost:8000/api/v1/health

---

## 二、数据流（数据库视角）

下面从「表」的角度看主干流程：一条资讯从接入到产出分析报告，依次经过哪些阶段、
每个阶段读写哪些表、事件如何在 `ingest_event` 队列里串联。表结构详见
[`docs/03-database-schema.md`](./docs/03-database-schema.md)。

### 2.1 核心表一览

| 分组 | 表 | 作用 |
| --- | --- | --- |
| 业务主数据 | `news_item` | 资讯主表，一条资讯一行，`status` 状态机贯穿全流程 |
| | `news_score` | 评分历史（可重算、可审计）；`news_item` 只存当前生效分 |
| | `news_chunk` | 向量分块（pgvector），检索与分析的历史记忆 |
| | `news_entity` | 资讯关联的标的/板块（评分时抽取） |
| | `analysis_report` | 深度分析报告 + 盘前/盘后简报（统一结构） |
| 事件队列 | `ingest_event` | 库内事件队列（Outbox/Inbox 合一），串联各阶段 |
| | `dead_letter` | 超过重试次数的事件，转存供人工重放 |
| 审计 | `llm_call_log` | 每次 LLM/Embedding 调用的成本与耗时审计 |
| 接入位点 | `ingest_cursor` | 每个数据源的增量拉取位点（cursor + 5min 重叠） |
| 行情缓存 | `market_daily` / `index_daily_bar` / `us_daily_bar` / `stock_daily` / `stock_daily_basic` / `top_list_bar` / `stock_forecast` / `stock_basic` / `sector` / `sector_member` / `trade_calendar` | 估值与走势分析、盘前/盘后的输入（**当前未同步，表为空**） |
| 追问 | `chat_session` / `chat_message` | 多轮问答会话 |
| 预留 | `agent_run` / `ingest_error` / `prompt_template` | 已建模，暂未实际写入 |

### 2.2 主干流程：一条资讯的数据流转

```
                          Tushare API（cls / wallstreetcn）
                                    │
  ① 接入 ingestion                 ▼
  ┌─────────────────────────────────────────────────────────────┐
  │ 读：ingest_cursor（取位点）                                    │
  │ 写：news_item(status=NEW)  +  ingest_cursor（推进位点）        │
  │ 发事件：news.ingested → ingest_event                          │
  └───────────────────────────┬─────────────────────────────────┘
                              │  worker 消费 news.ingested
  ② 评分 scoring              ▼
  ┌─────────────────────────────────────────────────────────────┐
  │ 读：news_item(status=NEW / SCORE_FAILED)                     │
  │ 写：news_score（历史） + news_entity（标的）                   │
  │     + news_item.score/band/reason + status=SCORED            │
  │ 审计：llm_call_log（flash 模型批量打分）                        │
  │ 分支：score≤3 → news_item.status=ARCHIVED_NOISE（终态，终止） │
  │ 发事件：news.scored（score>3）→ ingest_event                  │
  └───────────────────────────┬─────────────────────────────────┘
                              │  worker 消费 news.scored
  ③ 向量化 embedding          ▼
  ┌─────────────────────────────────────────────────────────────┐
  │ 读：news_item(status=SCORED)                                 │
  │ 写：news_chunk（分块 + 向量，先删后插幂等）                     │
  │     + news_item.status=EMBEDDED                              │
  │ 审计：llm_call_log（embedding 调用）                          │
  │ 发事件：news.embedded → ingest_event                         │
  └───────────────────────────┬─────────────────────────────────┘
                              │  worker 消费 news.embedded
  ④ 深度分析 analysis         ▼
  ┌─────────────────────────────────────────────────────────────┐
  │ 读：news_item(status=EMBEDDED)                               │
  │     + news_chunk（history_search 历史检索）                   │
  │     + 行情表（估值/走势，当前为空）                            │
  │ 按 band 路由：                                                │
  │   (7,10] macro_policy  (5,7] industry  (3,5] stock           │
  │ 写：analysis_report（结构化报告）+ news_item.status=ANALYZED   │
  │ 审计：llm_call_log（analysis 模型调用）                        │
  │ 发事件：analysis.published → ingest_event                    │
  └───────────────────────────┬─────────────────────────────────┘
                              ▼
                    前端 / 追问（chat）消费
```

### 2.3 各阶段参与的表（汇总）

| 阶段 | 触发 | 读表 | 写表 | 事件 | `news_item.status` 流转 |
| --- | --- | --- | --- | --- | --- |
| ① 接入 | APScheduler 每 60s | `ingest_cursor` | `news_item`、`ingest_cursor` | `news.ingested` | — → `NEW` |
| ② 评分 | 消费 `news.ingested` | `news_item` | `news_score`、`news_entity`、`news_item` | `news.scored` | `NEW` → `SCORING` → `SCORED`（≤3 分 → `ARCHIVED_NOISE`） |
| ③ 向量化 | 消费 `news.scored` | `news_item` | `news_chunk`、`news_item` | `news.embedded` | `SCORED` → `EMBEDDED` |
| ④ 深度分析 | 消费 `news.embedded` | `news_item`、`news_chunk`、行情表 | `analysis_report`、`news_item` | `analysis.published` | `EMBEDDED` → `ANALYZING` → `ANALYZED` |
| 盘前/盘后 | cron 07:30 / 15:30 | `market_daily`、`index_daily_bar`、`us_daily_bar`、`news_item` | `analysis_report`（`trade_date`+`period`） | — | 不涉及 |
| 追问 QA | `POST /chat/...` | `news_chunk`（向量检索）、`chat_session`、`chat_message` | `chat_session`、`chat_message` | — | 不涉及 |

每个阶段的模型调用都写 `llm_call_log`（provider/model/tokens/耗时/成本）。

### 2.4 事件队列贯穿全流程

`ingest_event` 是库内的 Outbox/Inbox 合一队列，数据与事件**同库同事务**（避免「数据已提交但事件丢失」）。

```
news.ingested ──► 评分 ──► news.scored ──► 向量化 ──► news.embedded ──► 深度分析 ──► analysis.published
```

- 软去重：`(event_type, aggregate_id)` 在 `PENDING/PROCESSING` 状态唯一，同一资讯同一阶段不重复排队
- 失败退避：`available_at` 指数退避 + `attempts` 计数，超 `max_attempts` 转 `dead_letter`
- 优先级：宏观(3) > 行业(2) > 个股(1)，`worker` 用 `FOR UPDATE SKIP LOCKED` 消费

---

## 三、目录结构

```
src/fin_news/
├── core/         配置、日志、DB 引擎、枚举、时间工具
├── domain/       纯函数：分档路由、分块、去重指纹、DTO
├── models/       SQLAlchemy 模型（news / event / analysis / chat）
├── ingestion/    Tushare 客户端、数据源、归一化、去重、过滤、位点、调度
├── events/       库内事件总线（publish / poll / ack / fail / 死信）
├── agents/       LLM 接入、Embedding、工具、Prompt 与 7 个 Agent
├── pipeline/     事件消费 worker + 三个处理器 + 攒批器
├── api/          FastAPI 应用、路由、响应模型、错误码
├── cli.py        运维命令
└── main.py       服务入口
```

---

## 四、常用命令

| 命令 | 说明 |
| --- | --- |
| `uv run python -m fin_news.cli selftest` | 数据源与模型凭据自检 |
| `uv run python -m fin_news.cli ingest` | 手动跑一次增量接入 |
| `uv run python -m fin_news.cli score` | 给待评分资讯打分（并自动补发下游事件） |
| `uv run python -m fin_news.cli embed` | **直接向量化**已评分资讯，不依赖事件队列（`--limit N`） |
| `uv run python -m fin_news.cli sweep` | 扫描状态与事件的不一致（dry-run）；`--apply` 实际修正 |
| `uv run python -m fin_news.cli pipeline` | 消费一轮事件（评分→向量化→分析） |
| `uv run python -m fin_news.cli worker` | 常驻 pipeline worker |
| `uv run python -m fin_news.cli premarket` | 生成当日盘前简报 |
| `uv run python -m fin_news.cli postmarket` | 生成当日盘后简报 |
| `uv run python -m fin_news.cli status` | 查看事件积压、资讯量、位点 |
| `uv run alembic revision --autogenerate -m "xxx"` | 生成迁移 |
| `docker compose logs -f db` | 查看数据库日志 |

---

## 五、说明与注意事项

1. **Tushare 资讯权限**：`news` 接口需单独开通资讯权限（与积分无关）。无权限时 `selftest` 会提示，接入模块会自动禁用该源并跳过调度。
2. **未配置模型 Key 时的行为**：资讯照常接入与落库；评分 / 向量化 / 分析步骤会跳过并把事件放回队列（`bus.release`），不会进死信。配置好 Key 重启即可继续。
3. **Embedding 维度**：`EMBEDDING_DIM` 必须与所选模型一致；不一致时向量化会直接报错终止，不会写入错误维度污染索引。
4. **行情数据尚未同步**：`market_daily` / `stock_daily` 等表由后续的市场数据同步任务填充，盘前/盘后 Agent 在行情为空时会基于资讯做定性分析。
