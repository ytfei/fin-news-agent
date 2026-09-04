// 与后端 src/fin_news/api/schemas.py 对齐的类型定义

export interface Page<T> {
  page: number;
  page_size: number;
  total: number;
  has_more: boolean;
  items: T[];
}

export interface Entity {
  type: string;
  code?: string | null;
  name?: string | null;
  confidence?: number | null;
}

export interface NewsItem {
  id: string;
  title: string;
  summary?: string | null;
  source: string;
  src?: string | null;
  src_name?: string | null;
  kind?: string | null;
  channels?: string | null;
  publish_time: string;
  ingested_at?: string | null;
  url?: string | null;
  score?: number | null;
  band?: string | null;
  score_reason?: string | null;
  tags: string[];
  entities: Entity[];
  has_analysis: boolean;
  analysis_summary?: string | null;
  analysis_id?: string | null;
  seen_count: number;
}

/** 渠道聚合项：资讯页顶部渠道标签的数据源（GET /news/sources） */
export interface NewsSource {
  src: string | null;
  src_name: string | null;
  count: number;
}

export interface NewsDetail extends NewsItem {
  content?: string | null;
  content_truncated: boolean;
  score_history: ScoreHistory[];
  related_news: RelatedNews[];
}

export interface ScoreHistory {
  score: number;
  band?: string | null;
  reason?: string | null;
  model?: string | null;
  prompt_version?: string | null;
  created_at?: string | null;
}

export interface RelatedNews {
  id: string;
  title: string;
  publish_time?: string | null;
  score?: number | null;
  similarity: number;
}

/** 深度分析列表项（GET /analysis/deep） */
export interface DeepAnalysisItem {
  id: string;
  agent_type: string;
  news_id?: string | null;
  news_title?: string | null;
  news_source?: string | null;
  news_publish_time?: string | null;
  title: string;
  summary: string;
  score?: number | null;
  band?: string | null;
  sentiment?: string | null;
  impact_level?: string | null;
  horizon?: string | null;
  confidence?: number | null;
  beneficiaries: ImpactTarget[];
  victims: ImpactTarget[];
  entities: Array<Record<string, unknown>>;
  bullets: string[];
  published_at?: string | null;
  disclaimer: string;
}

/** 简报历史归档项（GET /market/briefs） */
export interface BriefMeta {
  trade_date: string;
  period: string;
  report_id: string;
  title: string;
  summary: string;
  published_at?: string | null;
}

/** 简报统一包装（GET /market/brief）：无数据时 available=false，前端渲染空态而非报错 */
export interface BriefResponse {
  available: boolean;
  trade_date?: string | null;
  period: string;
  brief: PreMarketBrief | PostMarketBrief | null;
  message?: string | null;
}

export interface ImpactTarget {
  code?: string | null;
  name?: string | null;
  type: string;
  reason: string;
  direction: string;
}

export interface AnalysisReport {
  id: string;
  agent_type: string;
  news_id?: string | null;
  news_title?: string | null;
  trade_date?: string | null;
  title: string;
  summary: string;
  score?: number | null;
  band?: string | null;
  sentiment?: string | null;
  impact_level?: string | null;
  horizon?: string | null;
  confidence?: number | null;
  beneficiaries: ImpactTarget[];
  victims: ImpactTarget[];
  references: unknown[];
  status: string;
  model?: string | null;
  prompt_version?: string | null;
  published_at?: string | null;
  disclaimer: string;
  content?: Record<string, unknown>;
}

export interface IndexQuote {
  code: string;
  name: string;
  close?: number | null;
  pct_chg?: number | null;
}

export interface Breadth {
  advance?: number | null;
  decline?: number | null;
  flat?: number | null;
  limit_up?: number | null;
  limit_down?: number | null;
  total_amount?: number | null;
}

export interface SectorQuote {
  code: string;
  name?: string | null;
  pct_chg?: number | null;
}

export interface MarketOverview {
  trade_date: string;
  is_trading_day: boolean;
  indices: IndexQuote[];
  breadth?: Breadth | null;
  sectors_top: SectorQuote[];
  sectors_bottom: SectorQuote[];
  headline?: string | null;
}

export interface PostMarketBrief extends AnalysisReport {
  verdict: Record<string, unknown>;
  attribution: Array<Record<string, unknown>>;
  next_day_focus: string[];
}

export interface PreMarketBrief extends AnalysisReport {
  us_market: Array<Record<string, unknown>>;
  focus_directions: Array<Record<string, unknown>>;
}

export interface StockProfile {
  ts_code: string;
  name?: string | null;
  industry?: string | null;
  market?: string | null;
  latest?: Record<string, unknown> | null;
  trend: Array<Record<string, unknown>>;
}

export interface ChatSession {
  id: string;
  title?: string | null;
  context_filter: Record<string, unknown>;
  message_count: number;
  created_at?: string | null;
  last_message_at?: string | null;
}

export interface ChatMessage {
  id: string;
  session_id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  references: Array<Record<string, unknown>>;
  status: string;
  model?: string | null;
  latency_ms?: number | null;
  created_at?: string | null;
}

export interface EvalSet {
  id: number;
  public_id: string;
  name: string;
  description?: string;
  status: string;
  strategy: string;
  sample_size: number;
  total_items: number;
  labeled_items: number;
  exact_rate?: number | null;
  band_agree_rate?: number | null;
  mean_abs_diff?: number | null;
  confusion?: Record<string, Record<string, number>>;
  band_stats?: Record<string, { total: number; agree: number; rate: number }>;
  created_at?: string | null;
  completed_at?: string | null;
}

export interface EvalLabel {
  id: number;
  news_id: number;
  title: string;
  content: string;
  src_name?: string | null;
  publish_time?: string | null;
  model_score?: number | null;
  model_band?: string | null;
  model_reason?: string | null;
  human_score?: number | null;
  human_band?: string | null;
  human_note?: string | null;
  is_agree?: boolean | null;
}
