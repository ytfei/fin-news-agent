import { bandClass, bandLabel } from '../lib/band';

export function ScoreBadge({ score, band }: { score?: number | null; band?: string | null }) {
  if (score == null) {
    return <span className="score-badge band-unknown">—</span>;
  }
  return (
    <span className={`score-badge ${bandClass(band)}`} title={bandLabel(band)}>
      {score}
    </span>
  );
}

export function BandTag({ band }: { band?: string | null }) {
  if (!band) return null;
  return <span className={`band-tag ${bandClass(band)}`}>{bandLabel(band)}</span>;
}
