# fin-news-v5 文档索引

> 财经资讯分析 Agent：及时解释"市场为什么涨跌 + 接下来看什么"，并支持用户持续追问。

| 文档 | 内容 |
| --- | --- |
| [01-requirements.md](./01-requirements.md) | 需求文档（PRD）：背景、用户场景、功能需求 F1–F8、评分分档标准、非功能需求、MVP 验收、方案澄清与默认决策 |
| [02-architecture-and-flows.md](./02-architecture-and-flows.md) | 架构设计：技术栈、模块划分与目录结构、核心流程（接入/评分/向量化/分析/盘前盘后/追问）、状态机、模型抽象层、异常分支处置、并发限流、可观测与合规 |
| [03-database-schema.md](./03-database-schema.md) | 数据库表结构：PostgreSQL + pgvector 全部表、索引、约束、JSON 报告 schema、分区与幂等设计说明 |
| [04-openapi.md](./04-openapi.md) | OpenAPI 说明：接口总览、通用约定（分页/排序/错误码）、关键接口示例、前端消费约定、版本策略 |
| [openapi.yaml](./openapi.yaml) | OpenAPI 3.1 规范文件（唯一事实来源，可直接导入 Apifox/Postman 或生成客户端） |

## 一句话数据流

```
Tushare ──(每分钟增量)──► 归一化/去重 ──► news_item(NEW)
   └─► news.ingested 事件 ──► 批量评分(flash) ──► score>3 ? 向量化 : 归档噪声
        └─► news.embedded 事件 ──► 按分档路由：>7 宏观政策Agent（含联网）
                                              (5,7] 行业Agent（含估值）
                                              (3,5] 个股Agent
   cron 07:30 盘前Agent（隔夜美股+要闻）   cron 15:30 盘后Agent（归因复盘）
        └─► 全部结果落库 ──► REST API ──► Web(React/TS) / 未来 Flutter(iOS)
```

## 阅读顺序建议

1. 先看 `01` 的 §4 功能需求与 §9 澄清项（含需求原文的 3 处歧义与本方案默认决策）；
2. 再看 `02` 的 §3 模块结构、§4 核心流程、§7 异常分支处置表；
3. 数据库看 `03` 的 §2 接入侧与 §4 分析产物；
4. 接口看 `04` 的 §1 总览 + `openapi.yaml`。
