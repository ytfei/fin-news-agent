import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import type { MarketOverview, NewsItem, Page } from '../api/types';
import { ScoreBadge, BandTag } from '../components/ScoreBadge';
import { ErrorBox, Loading } from '../components/Common';
import { fmtPct, fmtTime, pctClass } from '../lib/band';

function OverviewCard({ data }: { data: MarketOverview }) {
  const b = data.breadth;
  return (
    <div className="card">
      <h3 className="card-title">市场概览 · {data.trade_date}</h3>
      <div className="grid">
        {data.indices.slice(0, 6).map((idx) => (
          <div className="stat" key={idx.code}>
            <div className="num muted" style={{ fontSize: 13 }} title={idx.name}>
              {idx.name}
            </div>
            <div className={`num ${pctClass(idx.pct_chg)}`}>{fmtPct(idx.pct_chg)}</div>
          </div>
        ))}
        {b && (
          <>
            <div className="stat">
              <div className="num up">{b.advance ?? '—'}</div>
              <div className="label">上涨</div>
            </div>
            <div className="stat">
              <div className="num down">{b.decline ?? '—'}</div>
              <div className="label">下跌</div>
            </div>
            <div className="stat">
              <div className="num up">{b.limit_up ?? '—'}</div>
              <div className="label">涨停</div>
            </div>
            <div className="stat">
              <div className="num">{b.total_amount ?? '—'}</div>
              <div className="label">成交额(亿)</div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export function Timeline() {
  const [minScore, setMinScore] = useState(4);
  const [sort, setSort] = useState<'impact' | 'publish_time' | 'score'>('impact');

  const overview = useQuery({
    queryKey: ['overview'],
    queryFn: () => api.get<MarketOverview>('/market/overview'),
  });

  const news = useQuery({
    queryKey: ['news', minScore, sort],
    queryFn: () =>
      api.get<Page<NewsItem>>(
        `/news?sort=${sort}&min_score=${minScore}&page=1&page_size=50`,
      ),
  });

  return (
    <div>
      {overview.isLoading ? <Loading /> : overview.error ? <ErrorBox error={overview.error} /> : overview.data && <OverviewCard data={overview.data} />}

      <div className="card">
        <div className="row" style={{ marginBottom: 12 }}>
          <h3 className="card-title" style={{ margin: 0 }}>
            今日时间线
          </h3>
          <span className="spacer" />
          <select value={sort} onChange={(e) => setSort(e.target.value as never)}>
            <option value="impact">按影响力</option>
            <option value="score">按评分</option>
            <option value="publish_time">按时间</option>
          </select>
          <select value={minScore} onChange={(e) => setMinScore(Number(e.target.value))}>
            <option value={0}>全部</option>
            <option value={4}>评分 ≥4</option>
            <option value={6}>评分 ≥6</option>
            <option value={8}>评分 ≥8</option>
          </select>
        </div>

        {news.isLoading ? (
          <Loading />
        ) : news.error ? (
          <ErrorBox error={news.error} />
        ) : !news.data?.items.length ? (
          <div className="empty">暂无资讯（可先运行 cli ingest / score 补充数据）</div>
        ) : (
          news.data.items.map((item) => (
            <div className="news-item" key={item.id}>
              <div className="news-head">
                <ScoreBadge score={item.score} band={item.band} />
                <div style={{ flex: 1 }}>
                  <div className="news-title">
                    {item.title}
                    <BandTag band={item.band} />
                  </div>
                  <div className="news-meta">
                    <span>{item.src_name || item.src || item.source}</span>
                    <span>{fmtTime(item.publish_time)}</span>
                    {item.seen_count > 1 && <span>重复 {item.seen_count} 次</span>}
                  </div>
                  {item.score_reason && <div className="news-reason">{item.score_reason}</div>}
                  {item.has_analysis && item.analysis_summary && (
                    <div className="news-reason" style={{ background: '#eff6ff' }}>
                      分析：{item.analysis_summary}
                    </div>
                  )}
                </div>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
