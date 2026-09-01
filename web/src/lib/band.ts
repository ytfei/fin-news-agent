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
