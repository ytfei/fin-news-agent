import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import type { EvalSet } from '../api/types';
import { ErrorBox, Loading } from '../components/Common';

function ratePct(v?: number | null): string {
  if (v == null) return '—';
  return `${(v * 100).toFixed(1)}%`;
}

function rateColor(v?: number | null): string {
  if (v == null) return 'var(--flat)';
  if (v >= 0.8) return 'var(--down)';
  if (v >= 0.6) return '#d97706';
  return 'var(--up)';
}

export function EvalSets() {
  const qc = useQueryClient();
  const [name, setName] = useState('人工评估');
  const [sampleSize, setSampleSize] = useState(100);

  const { data, isLoading, error } = useQuery({
    queryKey: ['eval-sets'],
    queryFn: () => api.get<{ items: EvalSet[]; total: number }>('/eval-sets?page=1&page_size=50'),
  });

  const create = useMutation({
    mutationFn: () =>
      api.post<{ id: number }>('/eval-sets', {
        name,
        description: '人工抽查分档，量化模型与人工一致率',
        sample_size: sampleSize,
        strategy: 'stratified_band',
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['eval-sets'] }),
  });

  if (isLoading) return <Loading />;
  if (error) return <ErrorBox error={error} />;

  return (
    <div>
      <div className="card">
        <h3 className="card-title">创建评估集</h3>
        <div className="row" style={{ flexWrap: 'wrap' }}>
          <input
            type="text"
            placeholder="评估集名称"
            value={name}
            onChange={(e) => setName(e.target.value)}
            style={{ width: 200 }}
          />
          <input
            type="number"
            value={sampleSize}
            min={1}
            max={2000}
            onChange={(e) => setSampleSize(Number(e.target.value))}
            style={{ width: 100 }}
          />
          <button className="primary" onClick={() => create.mutate()} disabled={create.isPending}>
            {create.isPending ? '抽样中…' : '分层抽样创建'}
          </button>
        </div>
        <div className="muted" style={{ marginTop: 8 }}>
          按「宏观 / 行业 / 个股 / 噪声」四档分层抽样，每档均分名额，保证各档都有样本。
        </div>
        {create.isError && <ErrorBox error={create.error} />}
      </div>

      <div className="card">
        <h3 className="card-title">评估集列表</h3>
        {!data?.items.length ? (
          <div className="empty">暂无评估集，点击上方「创建」抽样</div>
        ) : (
          data.items.map((s) => {
            const pct = s.total_items ? Math.round((s.labeled_items / s.total_items) * 100) : 0;
            return (
              <div className="news-item" key={s.id}>
                <Link to={`/eval/${s.id}`}>
                  <div className="news-head">
                    <div style={{ flex: 1 }}>
                      <div className="news-title">{s.name}</div>
                      <div className="news-meta">
                        <span>策略：{s.strategy === 'stratified_band' ? '分层抽样' : s.strategy}</span>
                        <span>
                          进度 {s.labeled_items}/{s.total_items}
                        </span>
                        <span>{s.status === 'DONE' ? '已完成' : s.status === 'IN_PROGRESS' ? '进行中' : '待标注'}</span>
                      </div>
                      <div className="progress">
                        <span style={{ width: `${pct}%` }} />
                      </div>
                    </div>
                    <div style={{ textAlign: 'right', minWidth: 110 }}>
                      <div className="muted">分档一致率</div>
                      <div style={{ fontSize: 20, fontWeight: 700, color: rateColor(s.band_agree_rate) }}>
                        {ratePct(s.band_agree_rate)}
                      </div>
                      {s.exact_rate != null && (
                        <div className="muted">分数一致率 {ratePct(s.exact_rate)}</div>
                      )}
                    </div>
                  </div>
                </Link>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
