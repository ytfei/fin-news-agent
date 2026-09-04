import { useNavigate } from 'react-router-dom';
import type { DeepAnalysisItem } from '../api/types';
import { BandTag, ScoreBadge } from './ScoreBadge';
import { agentLabel, fmtTime, horizonLabel, impactLabel, sentimentLabel } from '../lib/band';

export function AnalysisCard({ item }: { item: DeepAnalysisItem }) {
  const navigate = useNavigate();
  const sentiment = sentimentLabel(item.sentiment);
  const impact = impactLabel(item.impact_level);
  const horizon = horizonLabel(item.horizon);

  return (
    <article className="analysis-card" onClick={() => navigate(`/analysis/${item.id}`)}>
      <div className="card-head">
        <ScoreBadge score={item.score} band={item.band} />
        <div className="card-title-text">
          {item.title || item.news_title || '（无标题）'}
          <BandTag band={item.band} />
        </div>
      </div>

      <div className="chip-row">
        <span className={`chip ${sentiment.cls}`}>{sentiment.text}</span>
        <span className={`chip ${impact.cls}`}>{impact.text}</span>
        {horizon && <span className="chip">{horizon}</span>}
        <span className="chip">{agentLabel(item.agent_type)}</span>
        {item.confidence != null && (
          <span className="chip">置信度 {(item.confidence * 100).toFixed(0)}%</span>
        )}
      </div>

      <div className="card-summary clamp-3">{item.summary}</div>

      {item.bullets.length > 0 && (
        <ul className="bullets">
          {item.bullets.map((b, i) => (
            <li key={i}>{b}</li>
          ))}
        </ul>
      )}

      {item.news_title && (
        <div className="source-line">
          <span>源自：{item.news_title}</span>
        </div>
      )}
      <div className="source-line">
        {item.news_source && <span>{item.news_source}</span>}
        {item.news_publish_time && <span>· {fmtTime(item.news_publish_time)}</span>}
        <span className="spacer" />
        <span>{(item.beneficiaries?.length ?? 0) + (item.victims?.length ?? 0)} 个关联标的</span>
      </div>
    </article>
  );
}
