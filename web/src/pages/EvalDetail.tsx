import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { api } from '../api/client';
import type { EvalLabel, EvalSet } from '../api/types';
import { ErrorBox, Loading } from '../components/Common';
import { BandTag } from '../components/ScoreBadge';
import { bandClass } from '../lib/band';

const BANDS = ['MACRO', 'INDUSTRY', 'STOCK', 'NOISE'];

function ConfusionMatrix({ confusion }: { confusion?: Record<string, Record<string, number>> }) {
  if (!confusion || Object.keys(confusion).length === 0) return null;
  const humanBands = Array.from(new Set(Object.values(confusion).flatMap((row) => Object.keys(row)))).sort();
  return (
    <div className="card">
      <h3 className="card-title">混淆矩阵（行=模型分档，列=人工分档）</h3>
      <table style={{ borderCollapse: 'collapse', width: '100%', textAlign: 'center' }}>
        <thead>
          <tr>
            <th style={th}>模型\人工</th>
            {humanBands.map((b) => (
              <th key={b} style={th}>
                {b}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {Object.entries(confusion).map(([modelBand, row]) => (
            <tr key={modelBand}>
              <td style={{ ...th, background: '#f8fafc' }}>{modelBand}</td>
              {humanBands.map((hb) => {
                const v = row[hb] ?? 0;
                const agree = hb === modelBand;
                return (
                  <td
                    key={hb}
                    style={{
                      padding: 10,
                      border: '1px solid var(--border)',
                      background: v > 0 && agree ? '#dcfce7' : v > 0 ? '#fee2e2' : '#fff',
                      fontWeight: v > 0 ? 600 : 400,
                    }}
                  >
                    {v || ''}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const th: React.CSSProperties = { padding: 10, border: '1px solid var(--border)', fontSize: 13 };

export function EvalDetail() {
  const { id = '' } = useParams();
  const qc = useQueryClient();
  const [score, setScore] = useState<number | null>(null);

  const detail = useQuery({
    queryKey: ['eval-set', id],
    queryFn: () => api.get<EvalSet>(`/eval-sets/${id}`),
  });

  const labels = useQuery({
    queryKey: ['eval-labels', id],
    queryFn: () => api.get<{ items: EvalLabel[] }>(`/eval-sets/${id}/labels?only_unlabeled=true&page_size=50`),
  });

  const submit = useMutation({
    mutationFn: (label: EvalLabel) =>
      api.post(`/eval-sets/${id}/labels/${label.id}`, { human_score: score, labeled_by: 'web' }),
    onSuccess: () => {
      setScore(null);
      qc.invalidateQueries({ queryKey: ['eval-set', id] });
      qc.invalidateQueries({ queryKey: ['eval-labels', id] });
    },
  });

  if (detail.isLoading) return <Loading />;
  if (detail.error) return <ErrorBox error={detail.error} />;
  const ds = detail.data;
  if (!ds) return null;

  const pending = labels.data?.items ?? [];
  const current = pending[0];

  const done = ds.labeled_items >= ds.total_items && ds.total_items > 0;

  return (
    <div>
      <div className="card">
        <h3 className="card-title">{ds.name}</h3>
        <div className="row" style={{ flexWrap: 'wrap' }}>
          <div className="stat" style={{ minWidth: 120 }}>
            <div className="num" style={{ color: 'var(--accent)' }}>
              {((ds.band_agree_rate ?? 0) * 100).toFixed(1)}%
            </div>
            <div className="label">分档一致率（验收 ≥80%）</div>
          </div>
          <div className="stat" style={{ minWidth: 120 }}>
            <div className="num">{((ds.exact_rate ?? 0) * 100).toFixed(1)}%</div>
            <div className="label">分数一致率</div>
          </div>
          <div className="stat" style={{ minWidth: 120 }}>
            <div className="num">
              {ds.labeled_items}/{ds.total_items}
            </div>
            <div className="label">已标注 / 总数</div>
          </div>
        </div>
        <div className="progress">
          <span style={{ width: `${ds.total_items ? (ds.labeled_items / ds.total_items) * 100 : 0}%` }} />
        </div>
      </div>

      <ConfusionMatrix confusion={ds.confusion} />

      {ds.band_stats && Object.keys(ds.band_stats).length > 0 && (
        <div className="card">
          <h3 className="card-title">各分档一致率</h3>
          <div className="grid">
            {BANDS.map((band) => {
              const st = ds.band_stats?.[band];
              return (
                <div className="stat" key={band}>
                  <div className="label">
                    <span className={`band-tag ${bandClass(band)}`} style={{ margin: 0 }}>
                      {band}
                    </span>
                  </div>
                  <div className="num">
                    {st ? `${(st.rate * 100).toFixed(0)}%` : '—'}
                  </div>
                  <div className="label">{st ? `${st.agree}/${st.total} 一致` : '无样本'}</div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      <div className="card">
        <h3 className="card-title">人工抽查分档</h3>
        {labels.isLoading ? (
          <Loading />
        ) : done ? (
          <div className="empty">🎉 已全部标注完成！上方查看一致率统计。</div>
        ) : !current ? (
          <div className="empty">暂无待标注样本</div>
        ) : (
          <div>
            <div className="news-head">
              <div style={{ flex: 1 }}>
                <div className="news-title" style={{ fontSize: 16 }}>
                  {current.title}
                </div>
                <div className="news-meta">
                  <span>{current.src_name || '来源未知'}</span>
                  {current.publish_time && <span>{current.publish_time.slice(0, 16).replace('T', ' ')}</span>}
                </div>
              </div>
            </div>

            {current.content && (
              <div className="news-reason" style={{ marginTop: 10, maxHeight: 240, overflowY: 'auto', whiteSpace: 'pre-wrap' }}>
                {current.content}
              </div>
            )}

            <div className="news-reason" style={{ marginTop: 10, background: '#f0fdf4' }}>
              <strong>模型评分：</strong>
              {current.model_score} 分
              <BandTag band={current.model_band} />
              {current.model_reason && <div style={{ marginTop: 4 }}>{current.model_reason}</div>}
            </div>

            <div style={{ marginTop: 16 }}>
              <div className="muted" style={{ marginBottom: 8 }}>
                你的人工分档（1–10 分，自动归入宏观/行业/个股/噪声档）：
              </div>
              <div className="eval-score-options">
                {Array.from({ length: 10 }, (_, i) => i + 1).map((s) => (
                  <button
                    key={s}
                    className={score === s ? 'selected' : ''}
                    onClick={() => setScore(s)}
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>

            <div className="row" style={{ marginTop: 16 }}>
              <span className="spacer" />
              <span className="muted">剩余 {pending.length - 1} 条待标注</span>
              <button className="primary" disabled={score == null || submit.isPending} onClick={() => current && submit.mutate(current)}>
                {submit.isPending ? '提交中…' : '提交并下一条'}
              </button>
            </div>
            {submit.isError && <ErrorBox error={submit.error} />}
          </div>
        )}
      </div>
    </div>
  );
}
