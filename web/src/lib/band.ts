// 分档与分数 → 颜色 / 文案映射（与后端 ScoreBand 对齐）

export function bandClass(band?: string | null): string {
  switch (band) {
    case 'MACRO':
      return 'band-macro';
    case 'INDUSTRY':
      return 'band-industry';
    case 'STOCK':
      return 'band-stock';
    case 'NOISE':
      return 'band-noise';
    default:
      return 'band-unknown';
  }
}

export function bandLabel(band?: string | null): string {
  switch (band) {
    case 'MACRO':
      return '宏观';
    case 'INDUSTRY':
      return '行业';
    case 'STOCK':
      return '个股';
    case 'NOISE':
      return '噪声';
    default:
      return '未分档';
  }
}

/** 深度分析 Agent 类型 -> 中文（macro_policy / industry / stock） */
export function agentLabel(agentType?: string | null): string {
  switch (agentType) {
    case 'macro_policy':
      return '宏观';
    case 'industry':
      return '行业';
    case 'stock':
      return '个股';
    default:
      return '分析';
  }
}

/** 情绪 -> 中文 + 语义色 class */
export function sentimentLabel(sentiment?: string | null): { text: string; cls: string } {
  switch (sentiment) {
    case 'positive':
      return { text: '利好', cls: 'chip-positive' };
    case 'negative':
      return { text: '利空', cls: 'chip-negative' };
    case 'mixed':
      return { text: '多空交织', cls: 'chip-mixed' };
    case 'neutral':
      return { text: '中性', cls: 'chip-neutral' };
    default:
      return { text: '中性', cls: 'chip-neutral' };
  }
}

/** 影响程度 -> 中文 + 语义色 class */
export function impactLabel(level?: string | null): { text: string; cls: string } {
  switch (level) {
    case 'high':
      return { text: '影响高', cls: 'chip-high' };
    case 'low':
      return { text: '影响低', cls: 'chip-low' };
    case 'medium':
      return { text: '影响中', cls: 'chip-medium' };
    default:
      return { text: '影响中', cls: 'chip-medium' };
  }
}

/** 影响周期 -> 中文 */
export function horizonLabel(horizon?: string | null): string {
  switch (horizon) {
    case 'intraday':
      return '日内';
    case 'short':
      return '短期';
    case 'medium':
      return '中期';
    case 'long':
      return '长期';
    default:
      return '';
  }
}

/** 来源渠道展示名回退链：中文名 -> 渠道标识 -> 采集源 */
export function sourceLabel(
  item: { src_name?: string | null; src?: string | null; source?: string | null } | null | undefined,
): string {
  if (!item) return '未知来源';
  return item.src_name || item.src || item.source || '未知来源';
}

// A 股习惯：红涨绿跌
export function pctClass(value?: number | null): string {
  if (value == null) return 'flat';
  if (value > 0) return 'up';
  if (value < 0) return 'down';
  return 'flat';
}

export function fmtPct(value?: number | null): string {
  if (value == null) return '—';
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`;
}

export function fmtTime(iso?: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(
    d.getMinutes(),
  ).padStart(2, '0')}`;
}

/** YYYY-MM-DD（用于报告页日期选择与归档展示） */
export function fmtDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

/** 日期加减天数，返回 YYYY-MM-DD */
export function shiftDate(iso: string, days: number): string {
  const d = new Date(`${iso}T00:00:00`);
  d.setDate(d.getDate() + days);
  return fmtDate(d);
}

/** 成交额（元）-> 亿元文案 */
export function fmtAmount(yuan?: number | string | null): string {
  if (yuan == null || yuan === '') return '—';
  const n = Number(yuan);
  if (!Number.isFinite(n)) return String(yuan);
  return (n / 1e8).toFixed(0);
}
