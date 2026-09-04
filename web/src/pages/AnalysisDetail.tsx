import { Link, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import type { AnalysisReport, ImpactTarget } from '../api/types';
import { BandTag, ScoreBadge } from '../components/ScoreBadge';
import { Disclaimer, ErrorBox, Loading } from '../components/Common';
import { agentLabel, fmtTime, horizonLabel, impactLabel, sentimentLabel } from '../lib/band';

/** content 是 LLM 产出的 JSONB，结构不可信，统一按 unknown 逐层收敛 */
function strList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((v): v is string => typeof v === 'string' && v.trim().length > 0);
}

function ImpactColumn({ title, items }: { title: string; items: ImpactTarget[] }) {
  return (
    <div className="impact-col">
      <h4>{title}</h4>
      {items.length === 0 ? (
        <div className="muted">—</div>
      ) : (
        items.map((t, i) => (
          <div className="impact-item" key={i}>
            <div className="name">{t.name || t.code || '未具名标的'}</div>
            {t.reason && <div className="reason">{t.reason}</div>}
          </div>
        ))
      )}
    </div>
  );
}

export function AnalysisDetail() {
  const { id = '' } = useParams();
  const { data, isLoading, error } = useQuery({
    queryKey: ['analysis', id],
    queryFn: () => api.get<AnalysisReport>(`/analysis/${id}`),
    enabled: Boolean(id),
  });

  if (isLoading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (!data) return null;

  const content = (data.content ?? {}) as Record<string, unknown>;
  const bullets = strList(content.bullets);
  const logicChain = strList(content.logic_chain);
  const watchList = strList(content.watch_list);
  const risks = strList(content.risks);
  const sentiment = sentimentLabel(data.sentiment);
  const impact = impactLabel(data.impact_level);
  const horizon = horizonLabel(data.horizon);

  return (
    <div>
      <div className="card">
        <div className="row" style={{ marginBottom: 10 }}>
          <ScoreBadge score={data.score} band={data.band} />
          <BandTag band={data.band} />
          <span className="spacer" />
          <Link to="/deep" className="chip chip-medium">
            返回深度分析
          </Link>
        </div>
        <h2 style={{ margin: '6px 0 10px', fontSize: 19, fontWeight: 600, lineHeight: 1.5 }}>
          {data.title}
        </h2>
        <div className="chip-row">
          <span className={`chip ${sentiment.cls}`}>{sentiment.text}</span>
          <span className={`chip ${impact.cls}`}>{impact.text}</span>
          {horizon && <span className="chip">{horizon}</span>}
          <span className="chip">{agentLabel(data.agent_type)}</span>
          {data.confidence != null && (
            <span className="chip">置信度 {(data.confidence * 100).toFixed(0)}%</span>
          )}
        </div>

        {data.news_title && (
          <div className="news-reason" style={{ marginTop: 14 }}>
            <span className="muted">原始资讯 · </span>
            {data.news_title}
            {data.published_at && (
              <div className="muted" style={{ marginTop: 4 }}>
                {fmtTime(data.published_at)}
              </div>
            )}
          </div>
        )}
      </div>

      <div className="card">
        <div className="section-title" style={{ marginTop: 0 }}>
          分析摘要
        </div>
        <p style={{ margin: 0, lineHeight: 1.85, color: '#374151' }}>{data.summary}</p>
      </div>

      {logicChain.length > 0 && (
        <div className="card">
          <div className="section-title" style={{ marginTop: 0 }}>
            逻辑推演
          </div>
          <ol className="logic-chain">
            {logicChain.map((step, i) => (
              <li key={i}>{step}</li>
            ))}
          </ol>
        </div>
      )}

      {(bullets.length > 0 || watchList.length > 0) && (
        <div className="card">
          <div className="grid" style={{ gridTemplateColumns: '1fr 1fr', alignItems: 'start' }}>
            <div>
              <div className="section-title" style={{ marginTop: 0 }}>
                核心要点
              </div>
              <ul style={{ margin: 0, paddingLeft: 20, lineHeight: 1.9, fontSize: 14 }}>
                {bullets.map((b, i) => (
                  <li key={i}>{b}</li>
                ))}
              </ul>
            </div>
            <div>
              <div className="section-title" style={{ marginTop: 0 }}>
                观察清单
              </div>
              <ul style={{ margin: 0, paddingLeft: 20, lineHeight: 1.9, fontSize: 14 }}>
                {watchList.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      )}

      {(data.beneficiaries.length > 0 || data.victims.length > 0) && (
        <div className="card">
          <div className="section-title" style={{ marginTop: 0 }}>
            受益与受损标的
          </div>
          <div className="impact-grid">
            <ImpactColumn title="受益（利好）" items={data.beneficiaries} />
            <ImpactColumn title="受损（利空）" items={data.victims} />
          </div>
        </div>
      )}

      {risks.length > 0 && (
        <div className="card">
          <div className="section-title" style={{ marginTop: 0 }}>
            风险提示
          </div>
          <div className="risk-box">
            <ul style={{ margin: 0, paddingLeft: 20 }}>
              {risks.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          </div>
        </div>
      )}

      <div className="card">
        <div className="kv-row">
          <span className="kv-key">生成模型</span>
          <span className="kv-val">{data.model ?? '—'}</span>
        </div>
        <div className="kv-row">
          <span className="kv-key">提示词版本</span>
          <span className="kv-val">{data.prompt_version ?? '—'}</span>
        </div>
        <div className="kv-row">
          <span className="kv-key">发布时间</span>
          <span className="kv-val">{data.published_at ? fmtTime(data.published_at) : '—'}</span>
        </div>
        <div className="kv-row">
          <span className="kv-key">报告状态</span>
          <span className="kv-val">{data.status}</span>
        </div>
      </div>

      <Disclaimer />
    </div>
  );
}
