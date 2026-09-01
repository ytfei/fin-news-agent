import { useQuery } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { api } from '../api/client';
import type { NewsItem, Page, StockProfile } from '../api/types';
import { ErrorBox, Loading } from '../components/Common';
import { ScoreBadge } from '../components/ScoreBadge';
import { fmtTime } from '../lib/band';

function fmt(v: unknown): string {
  if (v == null) return '—';
  if (typeof v === 'number') return v.toFixed(2);
  return String(v);
}

export function StockDetail() {
  const { tsCode = '' } = useParams();

  const profile = useQuery({
    queryKey: ['stock', tsCode],
    queryFn: () => api.get<StockProfile>(`/stocks/${encodeURIComponent(tsCode)}`),
  });

  const news = useQuery({
    queryKey: ['stock-news', tsCode],
    queryFn: () => api.get<Page<NewsItem>>(`/stocks/${encodeURIComponent(tsCode)}/news?page=1&page_size=30`),
  });

  if (profile.isLoading) return <Loading />;
  if (profile.error) return <ErrorBox error={profile.error} />;
  const p = profile.data;
  if (!p) return null;

  const latest = (p.latest ?? {}) as Record<string, unknown>;

  return (
    <div>
      <div className="card">
        <h3 className="card-title" style={{ margin: 0 }}>
          {p.name || tsCode} <span className="muted">{tsCode}</span>
        </h3>
        <div className="muted" style={{ marginTop: 4 }}>
          {p.industry ?? '行业未知'} · {p.market ?? ''}
        </div>

        <div className="grid" style={{ marginTop: 14 }}>
          <div className="stat">
            <div className="num">{fmt(latest.close)}</div>
            <div className="label">收盘价</div>
          </div>
          <div className="stat">
            <div className="num">{fmt(latest.pct_chg)}%</div>
            <div className="label">涨跌幅</div>
          </div>
          <div className="stat">
            <div className="num">{fmt(latest.pe_ttm)}</div>
            <div className="label">PE(TTM)</div>
          </div>
          <div className="stat">
            <div className="num">{fmt(latest.pb)}</div>
            <div className="label">PB</div>
          </div>
          <div className="stat">
            <div className="num">{fmt(latest.total_mv)}</div>
            <div className="label">总市值(亿)</div>
          </div>
        </div>
        {!latest.pe_ttm && (
          <div className="muted" style={{ marginTop: 8 }}>
            估值数据来自行情同步，若为空说明该交易日行情尚未同步。
          </div>
        )}
      </div>

      <div className="card">
        <h3 className="card-title">相关资讯</h3>
        {news.isLoading ? (
          <Loading />
        ) : news.error ? (
          <ErrorBox error={news.error} />
        ) : !news.data?.items.length ? (
          <div className="empty">暂无与该标的相关的资讯</div>
        ) : (
          news.data.items.map((item) => (
            <div className="news-item" key={item.id}>
              <div className="news-head">
                <ScoreBadge score={item.score} band={item.band} />
                <div style={{ flex: 1 }}>
                  <div className="news-title">{item.title}</div>
                  <div className="news-meta">
                    <span>{item.src_name || item.src}</span>
                    <span>{fmtTime(item.publish_time)}</span>
                  </div>
                </div>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
