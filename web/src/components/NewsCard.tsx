import type { NewsItem } from '../api/types';
import { BandTag, ScoreBadge } from './ScoreBadge';
import { fmtTime, sourceLabel } from '../lib/band';

interface Props {
  item: NewsItem;
  view: 'list' | 'card';
  onOpen: (item: NewsItem) => void;
}

/** 列表态：紧凑行流，信息密度高，适合快速扫读 */
function ListRow({ item, onOpen }: { item: NewsItem; onOpen: (item: NewsItem) => void }) {
  return (
    <div className="news-item" onClick={() => onOpen(item)}>
      <div className="news-head">
        <ScoreBadge score={item.score} band={item.band} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="news-title">
            {item.title}
            <BandTag band={item.band} />
          </div>
          <div className="news-meta">
            <span>{sourceLabel(item)}</span>
            <span>{fmtTime(item.publish_time)}</span>
            {item.seen_count > 1 && <span>重复 {item.seen_count} 次</span>}
          </div>
          {item.score_reason && <div className="news-reason">{item.score_reason}</div>}
          {item.has_analysis && item.analysis_summary && (
            <div
              className="news-reason clamp-2"
              style={{ background: '#eff6ff', color: '#1e40af' }}
            >
              <strong>分析</strong> · {item.analysis_summary}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/** 卡片态：网格卡片，含摘要与标签，适合慢读 */
function GridCard({ item, onOpen }: { item: NewsItem; onOpen: (item: NewsItem) => void }) {
  return (
    <div className="news-card" onClick={() => onOpen(item)}>
      <div className="card-head">
        <ScoreBadge score={item.score} band={item.band} />
        <div className="card-title-text">
          {item.title}
          <BandTag band={item.band} />
        </div>
      </div>
      {item.summary && <div className="card-summary clamp-3">{item.summary}</div>}
      {item.has_analysis && item.analysis_summary && (
        <div className="news-reason clamp-2" style={{ background: '#eff6ff', color: '#1e40af' }}>
          <strong>分析</strong> · {item.analysis_summary}
        </div>
      )}
      <div className="source-line">
        <span>{sourceLabel(item)}</span>
        <span>·</span>
        <span>{fmtTime(item.publish_time)}</span>
        {item.has_analysis && <span className="chip chip-medium">已分析</span>}
      </div>
    </div>
  );
}

export function NewsCard({ item, view, onOpen }: Props) {
  return view === 'card' ? (
    <GridCard item={item} onOpen={onOpen} />
  ) : (
    <ListRow item={item} onOpen={onOpen} />
  );
}
