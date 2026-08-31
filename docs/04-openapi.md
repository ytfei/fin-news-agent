# fin-news-v5 OpenAPI 接口定义

- 规范文件：`docs/openapi.yaml`（OpenAPI 3.1，唯一事实来源）
- Base Path：`/api/v1`
- 协议：REST + JSON；追问接口支持 SSE 流式
- 鉴权（MVP）：Header `X-Device-Id`（匿名设备标识）。后续接入账号体系改为 Bearer JWT，**路径与载荷不变**，保证 Web 与未来 Flutter 端共用同一契约。

## 1. 接口总览

| 方法 | 路径 | 说明 | 前端用途 |
| --- | --- | --- | --- |
| GET | `/health` | 健康检查（DB/LLM/事件积压） | 运维 |
| GET | `/news` | 资讯流（分页/过滤/排序） | 首页时间线 |
| GET | `/news/{id}` | 资讯详情（含评分历史） | 详情页 |
| GET | `/news/{id}/analysis` | 该资讯的分析报告 | 详情页 |
| GET | `/news/{id}/related` | 语义相似历史资讯 | 详情页「历史上类似事件」 |
| GET | `/analysis` | 分析报告列表 | 深度分析页 |
| GET | `/analysis/{id}` | 报告详情（结构化正文） | 深度分析页 |
| GET | `/market/overview` | 市场概览 | 首页顶部 |
| GET | `/market/pre-market` | 盘前展望 | 盘前页 |
| GET | `/market/post-market` | 盘后复盘 + 归因 | 盘后页 |
| GET | `/market/briefs` | 最近 N 天简报元信息 | 日历视图 |
| GET | `/market/calendar` | 交易日历 | 日历视图 |
| POST | `/search` | 语义/混合检索 | 搜索页 |
| GET | `/stocks/{ts_code}` | 个股档案（估值 + 行情） | 个股页 |
| GET | `/stocks/{ts_code}/analysis` | 个股相关分析 | 个股页 |
| GET | `/stocks/{ts_code}/news` | 个股相关资讯 | 个股页 |
| GET | `/sectors` | 板块列表与表现 | 板块页 |
| GET/POST | `/chat/sessions` | 会话列表 / 创建 | 追问 |
| GET/DELETE | `/chat/sessions/{id}` | 会话详情 / 删除 | 追问 |
| GET | `/chat/sessions/{id}/messages` | 消息历史 | 追问 |
| POST | `/chat/sessions/{id}/messages` | 发起追问（SSE） | 追问 |
| POST | `/admin/ingest/backfill` | 区间补数 | 运维 |
| POST | `/admin/news/{id}/rescore` | 重算评分 | 运维 |
| POST | `/admin/news/{id}/reanalyze` | 重跑分析 | 运维 |
| GET | `/admin/events/backlog` | 积压与死信统计 | 运维 |
| POST | `/admin/dead-letter/{id}/replay` | 重放死信 | 运维 |

## 2. 通用约定

### 2.1 分页

请求：`page`（默认 1）、`page_size`（默认 20，最大 100）。

响应统一包裹：

```json
{ "page": 1, "page_size": 20, "total": 137, "has_more": true, "items": [ ... ] }
```

### 2.2 排序

`/news` 支持 `sort=publish_time|score|impact` + `order=asc|desc`。
`impact` = 评分与新鲜度的加权（`score * decay(publish_time)`），作为首页默认推荐的排序依据。

### 2.3 错误响应（RFC 9457）

```http
HTTP/1.1 400 Bad Request
Content-Type: application/problem+json
{
  "type": "https://api.example.com/errors/invalid-parameter",
  "title": "参数错误",
  "status": 400,
  "detail": "min_score 必须在 1 到 10 之间",
  "instance": "/api/v1/news?min_score=99",
  "trace_id": "9f1c2b...-..."
}
```

| 状态码 | 场景 |
| --- | --- |
| 400 | 参数校验失败 |
| 401 | 缺少/非法 `X-Device-Id` |
| 404 | 资源不存在（含"该交易日无简报"） |
| 429 | 触发限流（返回 `Retry-After`） |
| 500 | 未预期错误 |
| 503 | 依赖不可用（DB / LLM）；追问接口在 LLM 不可用时返回此码并附友好提示 |

### 2.4 时间与 ID

- 所有时间 ISO 8601，响应带时区偏移（展示层按 `Asia/Shanghai` 渲染）。
- 对外 ID 一律使用 `public_id`（uuid），不暴露自增主键。

## 3. 关键接口示例

### 3.1 资讯流

```http
GET /api/v1/news?band=MACRO,INDUSTRY&min_score=6&start=2026-09-01T00:00:00%2B08:00&sort=impact&page=1&page_size=20
X-Device-Id: 6f2e...
```

```json
{
  "page": 1, "page_size": 20, "total": 42, "has_more": true,
  "items": [
    {
      "id": "b1f0e2c4-9c1a-4a2e-9f77-6d1f6b9a2c11",
      "title": "央行：下调金融机构存款准备金率 0.5 个百分点",
      "summary": "央行宣布全面降准 0.5 个百分点，释放长期资金约 1 万亿元…",
      "src": "cls", "src_name": "财联社", "kind": "news",
      "publish_time": "2026-09-01T08:12:00+08:00",
      "score": 9, "band": "MACRO",
      "score_reason": "全面降准直接改善流动性，跨行业影响，历史上同类政策落地后 5 日指数胜率较高",
      "tags": ["货币政策", "降准"],
      "entities": [{"type": "macro", "code": "PBOC", "name": "中国人民银行", "confidence": 0.97}],
      "has_analysis": true,
      "analysis_summary": "流动性改善，关注券商、地产链与高股息的估值修复，出口链相对受损。",
      "analysis_id": "7c0b...-...",
      "seen_count": 1
    }
  ]
}
```

### 3.2 深度分析报告

```http
GET /api/v1/analysis/7c0b3a91-...
```

```json
{
  "id": "7c0b3a91-...",
  "agent_type": "macro_policy",
  "news_id": "b1f0e2c4-...",
  "news_title": "央行：下调金融机构存款准备金率 0.5 个百分点",
  "title": "全面降准 0.5pct：流动性改善，先修复估值、再看信用",
  "summary": "本次降准释放长期资金约 1 万亿元，属典型的逆周期总量工具…",
  "score": 9, "band": "MACRO",
  "sentiment": "positive", "impact_level": "high", "horizon": "short",
  "confidence": 0.78,
  "beneficiaries": [
    {"code": "BK0473", "name": "证券", "type": "sector", "direction": "positive", "reason": "流动性宽松直接提升成交额与两融"},
    {"code": "BK0451", "name": "房地产开发", "type": "sector", "direction": "positive", "reason": "融资端改善"}
  ],
  "victims": [
    {"code": "BK0486", "name": "出口链", "type": "sector", "direction": "negative", "reason": "人民币贬值预期扰动"}
  ],
  "content": {
    "headline": "总量宽松确认，短期交易估值修复，中期看信用传导",
    "bullets": ["释放长期资金约 1 万亿", "与 2024 年 9 月降准相比力度略弱但节奏更快"],
    "logic_chain": ["降准 → 银行负债成本下降 → 信贷投放意愿增强 → 风险偏好回升 → 成交额放大"],
    "market_impact": {"liquidity": "改善", "risk_appetite": "提升", "affected_markets": ["A股", "港股", "商品"]},
    "watch_list": ["9 月 LPR 报价", "8 月社融与 M1", "人民币汇率"],
    "disclaimer": "AI 生成，仅供参考，不构成投资建议。"
  },
  "external_sources": [{"title": "…", "url": "https://…", "publisher": "…", "published_at": "2026-09-01T…"}],
  "references": ["b1f0e2c4-…", "3a1f…"],
  "status": "PUBLISHED", "model": "volcengine/doubao-pro-…", "prompt_version": "macro.v3",
  "published_at": "2026-09-01T08:16:30+08:00"
}
```

### 3.3 盘后复盘（核心：回答"今天为什么涨跌"）

```http
GET /api/v1/market/post-market?date=2026-09-01
```

```json
{
  "id": "…", "agent_type": "post_market", "trade_date": "2026-09-01", "period": "post_market",
  "title": "9 月 1 日复盘：降准驱动普涨，成交放量至 1.2 万亿",
  "verdict": { "state": "up", "one_liner": "早盘降准落地直接点燃风险偏好，券商与地产链领涨，指数放量上行" },
  "attribution": [
    { "factor": "央行全面降准 0.5pct", "direction": "positive", "weight": 0.46, "news_ids": ["b1f0e2c4-…"] },
    { "factor": "隔夜美股科技股反弹，纳指 +1.1%", "direction": "positive", "weight": 0.21, "news_ids": ["…"] },
    { "factor": "8 月制造业 PMI 低于预期", "direction": "negative", "weight": 0.12, "news_ids": ["…"] }
  ],
  "market_stats": { "advance": 3980, "decline": 980, "limit_up": 86, "limit_down": 4, "total_amount": 12100 },
  "sectors_top": [{"code": "BK0473", "name": "证券", "pct_chg": 4.2}],
  "next_day_focus": ["成交额能否维持万亿以上", "LPR 报价", "券商板块是否二次放量"],
  "references": ["b1f0e2c4-…", "…"],
  "status": "PUBLISHED"
}
```

### 3.4 追问（SSE）

```http
POST /api/v1/chat/sessions/{id}/messages
Content-Type: application/json
{ "content": "降准对我的半导体持仓有什么影响？", "stream": true }
```

```
event: delta
data: {"text":"降准对半导体的影响偏间接，主要通过三条路径：…"}

event: references
data: {"items":[{"news_id":"b1f0e2c4-…","title":"央行：下调…","score":9,"snippet":"…"}]}

event: done
data: {"message_id":"1024","prompt_tokens":1820,"completion_tokens":460,"latency_ms":8120,"status":"OK"}
```

> `stream=false` 时直接返回 `ChatMessage` JSON（便于 App 端/弱网降级）。
> 资料不足时 `status=ABSTAINED`，正文明确说明"当前资料不足以判断"，并列出需要跟踪的信号。

## 4. 前端数据消费约定

1. **首页时间线**：`/market/overview` + `/news?sort=impact&min_score=4`，轮询 60s；高评分（≥8）资讯在列表内直接展示 `analysis_summary`。
2. **新鲜度提示**：前端按 `publish_time` 与 `has_analysis` 显示"分析中…"占位（分析 P95 ≤ 8 min）。
3. **简报缺失**：`/market/pre-market` 返回 404 时，前端展示"简报生成中"，并在 `updated_at` 后重试，不报错。
4. **免责声明**：任何展示 `summary` / `content` 的组件必须同时展示 `disclaimer`（组件层统一封装）。
5. **端无关**：所有字段语义与 App 端共用；`/chat` 的 SSE 事件格式在 Flutter 端用同一套解析逻辑。

## 5. 版本与兼容

- URL 前缀 `/api/v1` 为破坏性变更边界。
- 非破坏性变更（新增可选字段、新增枚举值）直接发布；前端需容忍未知枚举值。
- `analysis_report.content` 为 `additionalProperties: true` 的结构化对象：**新增字段不算破坏性变更**，消费方只取已知字段。
- `prompt_version` 随报告返回，便于定位某批结论使用的提示词版本。
