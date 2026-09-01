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
  publish_time: string;
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
