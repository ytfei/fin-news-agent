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
| 单元测试 `pytest` | 50 passed |
| 静态检查 `ruff` | All checks passed |
| **评分** | 已实跑：火山 `doubao-seed-2-0-mini` 批量打分，30 条/批约 12–45s |
| **向量化** | 已实跑：`doubao-embedding-text-240715`，2560 维，列类型 `halfvec(2560)` + HNSW |
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
| P3 分析 Agent | ⏳ 待做 | DeepAgents + 子 agent 并行 |
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

### 7. 启动服务（接入调度 + Pipeline + API）

```bash
uv run python -m fin_news.main
# 或：uv run uvicorn fin_news.api.app:app --reload
```

- API 文档：http://localhost:8000/docs
- 健康检查：http://localhost:8000/api/v1/health

---

## 二、数据流

```
Tushare news(src=cls/wallstreetcn)
   │  APScheduler 每 60s 增量拉取（cursor + 5min 重叠窗口）
   ▼
归一化 → 规则过滤 → 去重(content_hash + simhash) → news_item(status=NEW)
   │  发事件 news.ingested（与数据同事务）
   ▼
Pipeline worker（FOR UPDATE SKIP LOCKED 消费）
   │
   ├─ 批量评分（flash 模型，30 条/批）→ news_score + news_item.score
   │     score <= 3  → ARCHIVED_NOISE（不向量化、不分析）
   │     score >  3   → 发事件 news.scored
   │
   ├─ 分块 + Embedding → news_chunk(pgvector) → 发事件 news.embedded
   │
   └─ 按评分路由深度分析：
         (7,10]  macro_policy_agent（历史检索 + 外部检索）
         (5,7]   industry_agent（行业头部 + 估值）
         (3,5]   stock_agent（个股估值 + 走势）
         → analysis_report 落库

cron 07:30 盘前 Agent（隔夜美股 + 要闻 + 展望）
cron 15:30 盘后 Agent（涨跌归因 + 复盘）
   → analysis_report(trade_date, period)
```

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
| `uv run python -m fin_news.cli score` | 给待评分资讯打分 |
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
