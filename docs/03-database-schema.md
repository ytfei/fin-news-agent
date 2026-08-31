# fin-news-v5 数据库表结构

- 数据库：PostgreSQL 16 + `pgvector` 扩展
- ORM：SQLAlchemy 2.0（async）
- 迁移：Alembic（版本号与本文档 `schema_version` 对齐）
- 约定：
  - 主键统一 `bigint identity`（或 `bigserial`），对外暴露的 `id` 同时提供 `uuid` 列 `public_id` 供前端使用
  - 时间统一 `timestamptz`，存储 UTC；业务展示按 `Asia/Shanghai`
  - 枚举优先使用 PG `enum` + SQLAlchemy `Enum`（便于阅读）；高频变更的状态用 `varchar` + CHECK
  - JSON 半结构化字段统一 `jsonb` + GIN 索引
  - 向量维度由配置 `EMBEDDING_DIM` 生成（示例取 1024，**必须与所选 Embedding 模型一致**）

---

## 0. ER 概览

```
ingest_cursor ─┐
               ▼
            news_item ──1:N──> news_chunk (vector)
               │ 1:N
               ├──> news_score            (评分历史/可重算)
               ├──> analysis_report  N:1 ─ agent_run
               └──> news_entity           (关联标的/板块)
ingest_event (队列) ── 指向 news_item / analysis_report
agent_run    (运行记录)   llm_call_log (模型调用审计)
market_daily / index_bar / stock_daily / us_daily_bar  (行情缓存)
sector / sector_member                     (板块与成分)
chat_session ─1:N─> chat_message
```

## 1. 扩展与 Schema

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;   -- 中文标题模糊检索兜底

CREATE TYPE score_band      AS ENUM ('NOISE','STOCK','INDUSTRY','MACRO');
CREATE TYPE news_status     AS ENUM ('NEW','SCORING','SCORED','ARCHIVED_NOISE','EMBEDDING',
                                     'EMBEDDED','ANALYZING','ANALYZED',
                                     'SCORE_FAILED','EMBED_FAILED','ANALYSIS_FAILED','DEAD');
CREATE TYPE agent_type      AS ENUM ('scoring','macro_policy','industry','stock',
                                     'pre_market','post_market','qa');
CREATE TYPE run_status      AS ENUM ('PENDING','RUNNING','SUCCESS','FAILED','TIMEOUT','CANCELLED','DEAD');
CREATE TYPE event_status    AS ENUM ('PENDING','PROCESSING','DONE','FAILED');
CREATE TYPE report_status   AS ENUM ('DRAFT','PUBLISHED','DEGRADED','SUPERSEDED');
CREATE TYPE ingest_kind     AS ENUM ('news','major_news','anns','forecast','top_list','market');
```

---

## 2. 接入侧

### 2.1 `ingest_cursor` — 增量位点

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigserial PK | |
| `source_key` | varchar(64) UNIQUE NOT NULL | 如 `tushare.news.cls`、`tushare.major_news.sina` |
| `kind` | ingest_kind NOT NULL | |
| `cursor_time` | timestamptz NOT NULL | 已成功处理到的时间点 |
| `overlap_seconds` | int NOT NULL DEFAULT 300 | 回退重叠窗口 |
| `last_run_at` / `last_success_at` | timestamptz | |
| `last_status` | varchar(16) | OK / PARTIAL / FAILED |
| `last_error` | text | |
| `last_count` | int | 上一轮入库条数 |
| `enabled` | bool NOT NULL DEFAULT true | 无权限/废弃源可关闭 |
| `updated_at` | timestamptz DEFAULT now() | |

### 2.2 `news_item` — 资讯主表

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigserial PK | |
| `public_id` | uuid UNIQUE NOT NULL DEFAULT gen_random_uuid() | 对外 ID |
| `source` | varchar(32) NOT NULL | `tushare` |
| `source_key` | varchar(64) NOT NULL | `tushare.news.cls` |
| `kind` | ingest_kind NOT NULL | |
| `src` | varchar(32) | 原始来源标识（sina/cls/yicai...） |
| `external_id` | varchar(128) | 数据源侧 ID（部分源无，则由 hash 兜底） |
| `title` | text NOT NULL | |
| `content` | text | 正文（长篇通讯可为空，需二次抓取） |
| `content_truncated` | bool DEFAULT false | |
| `channels` | varchar(64) | 原站分类 |
| `url` | text | 原文链接（有则用） |
| `publish_time` | timestamptz NOT NULL | 资讯发布时间 |
| `ingested_at` | timestamptz NOT NULL DEFAULT now() | |
| `content_hash` | char(64) NOT NULL | 规范化正文 SHA-256（去重键 1） |
| `simhash` | bigint | 近似去重指纹（去重键 2） |
| `seen_count` | int NOT NULL DEFAULT 1 | 重复出现次数 |
| `first_seen_at` | timestamptz NOT NULL DEFAULT now() | |
| `score` | smallint | 当前生效评分（1–10） |
| `band` | score_band | 当前生效分档（冗余，便于索引过滤） |
| `score_reason` | text | 当前评分理由 |
| `score_model` | varchar(64) | |
| `score_version` | varchar(32) | prompt 版本 |
| `scored_at` | timestamptz | |
| `status` | news_status NOT NULL DEFAULT 'NEW' | |
| `analysis_status` | varchar(16) | `NONE/PENDING/DONE/FAILED`（派生冗余，便于查询） |
| `retry_count` | int NOT NULL DEFAULT 0 | |
| `last_error` | text | |
| `dedup_of` | bigint | 指向被去重合并到的 `news_item.id` |
| `metadata` | jsonb NOT NULL DEFAULT '{}' | 原接口残留字段（src_site 等） |

约束与索引：
```sql
CREATE UNIQUE INDEX uq_news_dedup ON news_item (source_key, content_hash);
CREATE INDEX idx_news_simhash      ON news_item (simhash);
CREATE INDEX idx_news_publish_desc ON news_item (publish_time DESC);
CREATE INDEX idx_news_score_time   ON news_item (score DESC, publish_time DESC) WHERE score IS NOT NULL;
CREATE INDEX idx_news_band_time    ON news_item (band, publish_time DESC) WHERE band IS NOT NULL;
CREATE INDEX idx_news_status       ON news_item (status) WHERE status NOT IN ('ANALYZED','ARCHIVED_NOISE');
CREATE INDEX idx_news_title_trgm   ON news_item USING gin (title gin_trgm_ops);
```
> 数据量增长后按 `publish_time` 做**声明式月分区**；`content_hash` 唯一索引需含分区键。

### 2.3 `news_score` — 评分历史（可重算、可审计）

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigserial PK | |
| `news_id` | bigint NOT NULL REFERENCES news_item(id) ON DELETE CASCADE | |
| `score` | smallint NOT NULL | CHECK `score BETWEEN 1 AND 10` |
| `band` | score_band NOT NULL | 由 score 推导 |
| `reason` | text | |
| `tags` | jsonb | `["货币政策","美联储"]` |
| `confidence` | numeric(3,2) | 0–1 |
| `is_suspect` | bool DEFAULT false | 批量分布异常标记 |
| `model` | varchar(64) NOT NULL | |
| `prompt_version` | varchar(32) NOT NULL | |
| `batch_id` | uuid | 批量评分批次 |
| `latency_ms` | int | |
| `created_at` | timestamptz DEFAULT now() | |

```sql
CREATE INDEX idx_score_news ON news_score (news_id, created_at DESC);
```

### 2.4 `news_chunk` — 向量分块（pgvector）

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigserial PK | |
| `news_id` | bigint NOT NULL REFERENCES news_item(id) ON DELETE CASCADE | |
| `chunk_index` | int NOT NULL | |
| `content` | text NOT NULL | 嵌入文本（含标题/时间/来源前缀） |
| `token_count` | int | |
| `embedding` | vector(1024) NOT NULL | 维度由 `EMBEDDING_DIM` 决定 |
| `score` | smallint | 冗余，便于检索时过滤加权 |
| `publish_time` | timestamptz | 冗余，便于时间过滤 |
| `band` | score_band | 冗余 |
| `entity_codes` | text[] | 关联标的代码，便于按票过滤 |
| `model` | varchar(64) NOT NULL | embedding 模型 |
| `created_at` | timestamptz DEFAULT now() | |

```sql
CREATE UNIQUE INDEX uq_chunk ON news_chunk (news_id, chunk_index);
CREATE INDEX idx_chunk_embedding ON news_chunk
  USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
CREATE INDEX idx_chunk_news    ON news_chunk (news_id);
CREATE INDEX idx_chunk_entities ON news_chunk USING gin (entity_codes);
-- 运行时可调召回质量：SET hnsw.ef_search = 100;
```

### 2.5 `news_entity` — 资讯关联标的/板块（由评分 Agent 抽取）

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigserial PK | |
| `news_id` | bigint NOT NULL REFERENCES news_item(id) ON DELETE CASCADE | |
| `entity_type` | varchar(16) | `stock` / `sector` / `index` / `macro` |
| `code` | varchar(32) | `600519.SH`、`BK0447` |
| `name` | varchar(64) | |
| `confidence` | numeric(3,2) | |

```sql
CREATE INDEX idx_entity_news ON news_entity (news_id);
CREATE INDEX idx_entity_code ON news_entity (code, publish_date DESC);
-- publish_date 冗余列便于按"某只票最近相关资讯"查询
```

---

## 3. 事件与任务

### 3.1 `ingest_event` — 库内事件队列（Outbox/Inbox 合一）

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigserial PK | |
| `event_type` | varchar(48) NOT NULL | `news.ingested` / `news.scored` / `news.embedded` / `analysis.published` |
| `aggregate_type` | varchar(32) NOT NULL | `news_item` / `analysis_report` |
| `aggregate_id` | bigint NOT NULL | |
| `payload` | jsonb NOT NULL DEFAULT '{}' | 轻量上下文（score、band、source_key） |
| `status` | event_status NOT NULL DEFAULT 'PENDING' | |
| `priority` | smallint NOT NULL DEFAULT 1 | 1 个股 / 2 行业 / 3 宏观 / 5 盘前盘后 |
| `available_at` | timestamptz NOT NULL DEFAULT now() | 退避调度 |
| `attempts` | int NOT NULL DEFAULT 0 | |
| `max_attempts` | int NOT NULL DEFAULT 5 | |
| `locked_by` | varchar(64) | worker 实例 |
| `locked_at` | timestamptz | |
| `last_error` | text | |
| `created_at` / `processed_at` | timestamptz | |

```sql
CREATE INDEX idx_event_poll ON ingest_event (priority DESC, created_at)
  WHERE status = 'PENDING';
CREATE INDEX idx_event_available ON ingest_event (available_at) WHERE status = 'PENDING';
CREATE INDEX idx_event_agg ON ingest_event (aggregate_type, aggregate_id, event_type);
-- 防止同一聚合重复排队（软去重）
CREATE UNIQUE INDEX uq_event_pending_dedup ON ingest_event (event_type, aggregate_id)
  WHERE status IN ('PENDING','PROCESSING');
```
> DONE/FAILED 事件按月分区并保留 7 天后清理。

### 3.2 `agent_run` — Agent 运行记录

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigserial PK | |
| `run_id` | uuid UNIQUE NOT NULL DEFAULT gen_random_uuid() | |
| `agent_type` | agent_type NOT NULL | |
| `subject_type` | varchar(32) NOT NULL | `news_item` / `trade_date` / `chat_session` |
| `subject_id` | varchar(64) | news_id 或 `2026-09-01` |
| `status` | run_status NOT NULL DEFAULT 'PENDING' | |
| `attempt` | int NOT NULL DEFAULT 1 | |
| `priority` | smallint NOT NULL DEFAULT 1 | |
| `model` | varchar(64) | 实际使用的模型（含 fallback 记录） |
| `prompt_version` | varchar(32) | |
| `input_digest` | char(64) | 输入指纹（幂等去重） |
| `payload` | jsonb | 输入快照 |
| `result_ref` | bigint | 指向 `analysis_report.id` |
| `latency_ms` / `prompt_tokens` / `completion_tokens` / `cost_cent` | int/numeric | |
| `error_type` / `error_message` | varchar(64) / text | |
| `trace_id` | varchar(64) | |
| `scheduled_at` / `started_at` / `finished_at` | timestamptz | |

```sql
CREATE UNIQUE INDEX uq_run_idem ON agent_run (agent_type, subject_id, prompt_version, input_digest);
CREATE INDEX idx_run_status ON agent_run (status, started_at DESC);
CREATE INDEX idx_run_subject ON agent_run (subject_type, subject_id, created_at DESC);
```

### 3.3 `llm_call_log` — 模型调用审计

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigserial PK | |
| `trace_id` | varchar(64) | |
| `run_id` | uuid | 关联 `agent_run.run_id` |
| `provider` | varchar(32) | `volcengine` / `deepseek` |
| `role` | varchar(32) | `scoring` / `analysis` / `qa` / `embedding` |
| `model` | varchar(64) | |
| `is_fallback` | bool DEFAULT false | |
| `request_chars` / `prompt_tokens` / `completion_tokens` | int | |
| `latency_ms` / `ttft_ms` | int | |
| `status` | varchar(16) | OK / ERROR / TIMEOUT |
| `error_message` | text | |
| `cost_cent` | numeric(10,4) | 估算成本（分） |
| `created_at` | timestamptz DEFAULT now() | |

```sql
CREATE INDEX idx_llm_created ON llm_call_log (created_at DESC);
CREATE INDEX idx_llm_role_day ON llm_call_log (role, created_at DESC);
```

---

## 4. 分析产物

### 4.1 `analysis_report` — 分析报告（统一结构）

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigserial PK | |
| `public_id` | uuid UNIQUE NOT NULL DEFAULT gen_random_uuid() | |
| `agent_type` | agent_type NOT NULL | |
| `news_id` | bigint REFERENCES news_item(id) ON DELETE CASCADE | 个股/行业/宏观分析非空 |
| `trade_date` | date | 盘前/盘后简报非空 |
| `period` | varchar(16) | `pre_market` / `post_market`（简报用） |
| `title` | varchar(255) NOT NULL | |
| `summary` | text NOT NULL | 1–3 句结论（列表页展示） |
| `content` | jsonb NOT NULL | 结构化正文（见下方 schema） |
| `score` | smallint | 资讯评分快照 |
| `band` | score_band | |
| `sentiment` | varchar(16) | `positive` / `negative` / `neutral` / `mixed` |
| `impact_level` | varchar(16) | `high` / `medium` / `low` |
| `horizon` | varchar(16) | `intraday` / `short` / `medium` / `long` |
| `confidence` | numeric(3,2) | 0–1 |
| `beneficiaries` | jsonb | `[{code,name,reason}]` 受益板块/标的 |
| `victims` | jsonb | `[{code,name,reason}]` 受损板块/标的 |
| `entities` | jsonb | `[{code,name,type}]` |
| `references` | jsonb | 引用的 `news_id[]` |
| `external_sources` | jsonb | `[{title,url,publisher,published_at}]` |
| `status` | report_status NOT NULL DEFAULT 'DRAFT' | |
| `model` / `prompt_version` | varchar | |
| `run_id` | uuid | |
| `tokens` / `latency_ms` / `cost_cent` | int/numeric | |
| `published_at` | timestamptz | |
| `created_at` / `updated_at` | timestamptz | |

约束与索引：
```sql
-- 幂等：同一资讯 + 同一 Agent + 同一 prompt 版本只保留一份
CREATE UNIQUE INDEX uq_report_news_agent ON analysis_report (news_id, agent_type, prompt_version)
  WHERE status IN ('DRAFT','PUBLISHED','DEGRADED');
-- 简报：同一交易日同一场次只保留一份
CREATE UNIQUE INDEX uq_report_brief ON analysis_report (trade_date, period, prompt_version)
  WHERE period IN ('pre_market','post_market');
CREATE INDEX idx_report_pub ON analysis_report (published_at DESC) WHERE status = 'PUBLISHED';
CREATE INDEX idx_report_type_time ON analysis_report (agent_type, published_at DESC);
CREATE INDEX idx_report_band ON analysis_report (band, published_at DESC);
CREATE INDEX idx_report_entities ON analysis_report USING gin (entities jsonb_path_ops);
CREATE INDEX idx_report_trade_date ON analysis_report (trade_date DESC);
```

`content` 的 JSON 结构（按 Agent 类型略有差异，统一外层）：

```jsonc
{
  "headline": "一句话结论",
  "bullets": ["要点1", "要点2"],           // 3-6 条
  "logic_chain": ["事件→传导→结果"],        // 因果链，宏观/行业必填
  "market_impact": {                        // 宏观
    "liquidity": "改善/收紧/中性",
    "risk_appetite": "提升/下降/中性",
    "affected_markets": ["A股","港股","美股","商品"]
  },
  "industry_impact": {                      // 行业
    "sector": "半导体",
    "direction": "positive",
    "leaders": [{ "code": "688981.SH", "name": "中芯国际", "pe_ttm": 88.1, "pb": 3.2, "pe_percentile": 0.72 }],
    "valuation_comment": "…"
  },
  "stock_impact": {                         // 个股
    "code": "300308.SZ",
    "valuation": { "pe_ttm": 40.2, "pb": 5.1, "ps_ttm": 6.3, "percentile_3y": 0.61 },
    "trend": { "ma5": 12.3, "ma20": 11.8, "ret_5d": 0.08, "vol_ratio": 1.6 },
    "catalysts": ["…"], "risks": ["…"]
  },
  "watch_list": ["关键跟踪信号1", "信号2"],
  "disclaimer": "AI 生成，仅供参考，不构成投资建议。"
}
```

盘前/盘后简报的 `content` 追加：
```jsonc
{
  "us_market": [{ "symbol": ".IXIC", "name": "纳斯达克", "pct_chg": -0.82, "close": 17880.1 }],
  "overnight_top_news": [{ "news_id": 123, "title": "…", "score": 8 }],
  "attribution": [                       // 盘后：归因，贡献度降序
    { "factor": "美联储降息预期升温", "direction": "positive", "weight": 0.42, "news_ids": [88, 91] }
  ],
  "market_stats": { "advance": 3120, "decline": 1450, "amount": 11800, "limit_up": 62 },
  "sectors_top": [{ "code": "BK0447", "name": "半导体", "pct_chg": 3.1 }],
  "sectors_bottom": [{ "code": "BK0725", "name": "白酒", "pct_chg": -1.8 }],
  "next_day_focus": ["…"]
}
```

### 4.2 `market_daily` — 每日市场快照（盘前/盘后 Agent 的输入缓存）

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `trade_date` | date PK | |
| `is_trading_day` | bool NOT NULL | 来自 `trade_cal` |
| `index_bars` | jsonb | `[{code,name,open,high,low,close,pct_chg,amount}]` |
| `advance` / `decline` / `flat` | int | 涨跌平家数 |
| `limit_up` / `limit_down` | int | |
| `total_amount` | numeric(18,2) | 两市成交额（亿元） |
| `northbound` | numeric(18,2) | 北向（若接口可用） |
| `sectors_top` / `sectors_bottom` | jsonb | |
| `us_overnight` | jsonb | 隔夜美股关键指数与权重股 |
| `stats_ready` | bool DEFAULT false | 数据齐备标志（盘后任务前置检查） |
| `updated_at` | timestamptz | |

### 4.3 行情缓存表（供估值/走势分析，按 Tushare 结构落库）

| 表 | 关键列 | 说明 |
| --- | --- | --- |
| `stock_daily` | `ts_code, trade_date, open, high, low, close, vol, amount, pct_chg`（PK: ts_code+trade_date） | `daily` |
| `stock_daily_basic` | `ts_code, trade_date, close, turnover_rate, volume_ratio, pe_ttm, pb, ps_ttm, dv_ttm, total_mv, circ_mv`（PK 同上） | `daily_basic` |
| `index_daily_bar` | `ts_code, trade_date, close, pct_chg, amount` | `index_daily` |
| `us_daily_bar` | `ts_code, trade_date, close, pct_chg, pe, pb, total_mv` | `us_daily` |
| `top_list_bar` | `trade_date, ts_code, name, close, pct_chg, buy_amount, sell_amount, net_amount, reason` | `top_list` |
| `stock_forecast` | `ts_code, ann_date, end_date, type, p_change_min, p_change_max, net_profit_min` | `stk_forecast` |
| `fina_indicator_snapshot` | `ts_code, end_date, eps, roe, grossprofit_margin, debt_to_assets` | `fina_indicator` |
| `stock_basic` | `ts_code, name, industry, market, list_date` | 名称与行业映射 |
| `sector` / `sector_member` | `code, name, type` / `sector_code, ts_code` | 板块与成分（Tushare/同花顺概念） |
| `trade_calendar` | `exchange, cal_date, is_open` | `trade_cal` |

---

## 5. 追问（Chat）

### 5.1 `chat_session`

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigserial PK | |
| `public_id` | uuid UNIQUE NOT NULL DEFAULT gen_random_uuid() | |
| `device_id` / `user_id` | varchar(64) / bigint | MVP 用 device_id |
| `title` | varchar(255) | 首问自动生成 |
| `context_filter` | jsonb | `{start_date,end_date,band,codes}` 会话级检索过滤 |
| `message_count` | int DEFAULT 0 | |
| `last_message_at` | timestamptz | |
| `created_at` | timestamptz DEFAULT now() | |

### 5.2 `chat_message`

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigserial PK | |
| `session_id` | bigint NOT NULL REFERENCES chat_session(id) ON DELETE CASCADE | |
| `role` | varchar(16) NOT NULL | `user` / `assistant` / `system` |
| `content` | text NOT NULL | |
| `references` | jsonb | 引用的 `news_id[]` / `analysis_report_id[]` |
| `retrieved_chunk_ids` | bigint[] | 召回记录，便于排查 |
| `tool_calls` | jsonb | 简化的工具调用轨迹 |
| `model` / `prompt_version` | varchar | |
| `prompt_tokens` / `completion_tokens` / `latency_ms` | int | |
| `status` | varchar(16) | `OK` / `FAILED` / `ABSTAINED`（资料不足未作答） |
| `created_at` | timestamptz DEFAULT now() | |

```sql
CREATE INDEX idx_msg_session ON chat_message (session_id, id);
```

---

## 6. 运维表

### 6.1 `dead_letter`

| 列 | 说明 |
| --- | --- |
| `id, source_table, source_id, event_type, error_type, error_message, payload(jsonb), attempts, created_at, resolved_at` | 超过 `max_attempts` 的事件/任务转存此处，供人工重放 |

### 6.2 `prompt_template`（可选，用于 prompt 版本化与热更新）

| 列 | 说明 |
| --- | --- |
| `id, agent_type, version, system_prompt, user_template, response_schema(jsonb), is_active, created_at` | 与 `prompt_version` 关联；上线新 prompt 时不重算历史，仅在重跑时生效 |

### 6.3 `ingest_error`

| 列 | 说明 |
| --- | --- |
| `id, source_key, kind, payload(jsonb), error_message, created_at` | 单条脏数据/单源失败留痕，不阻断主流程 |

---

## 7. 关键设计说明

1. **不引入外部 MQ**：事件表 + `FOR UPDATE SKIP LOCKED` 即可满足分钟级吞吐（千级 QPS 以下），且与业务数据同库同事务，避免"数据已提交但事件丢失"或反之。规模上来后可平滑替换为 Kafka/RabbitMQ（只需替换 `events/bus.py` 实现）。
2. **幂等三处保障**：事件表软去重唯一索引、`agent_run.input_digest` 唯一键、`analysis_report(news_id, agent_type, prompt_version)` 部分唯一索引。
3. **冗余列的价值**：`news_chunk` 冗余 `score/band/publish_time/entity_codes`，让"向量检索 + 结构化过滤"在单表完成，避免大 JOIN。
4. **评分可重算**：`news_item` 只存当前生效值，`news_score` 存全部历史；换模型只需重跑评分并回写，不破坏已有分析。
5. **向量维度治理**：`EMBEDDING_DIM` 变更必须走 Alembic 迁移（改列类型 + 重建 HNSW 索引 + 全量重嵌），禁止线上直接改列。
6. **分区策略**：`news_item`、`news_chunk`、`ingest_event`、`llm_call_log` 按时间月分区（`news_chunk` 通过 `news_id` 关联时间，必要时冗余 `publish_date` 作为分区键）。MVP 数据量小可先不分区，索引与 DDL 预留。
