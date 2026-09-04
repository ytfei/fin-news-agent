import { useState } from 'react';
import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import type { MarketOverview, NewsItem, NewsSource, Page } from '../api/types';
import { ChannelTabs } from '../components/ChannelTabs';
import { NewsCard } from '../components/NewsCard';
import { NewsDrawer } from '../components/NewsDrawer';
import { Disclaimer, Empty, ErrorBox, Loading } from '../components/Common';
import { fmtAmount, fmtPct, pctClass } from '../lib/band';

type SortKey = 'impact' | 'score' | 'publish_time';
type ViewMode = 'list' | 'card';

const PAGE_SIZE = 30;
const VIEW_STORAGE_KEY = 'fin-news-feed-view';

const SORT_OPTIONS: Array<{ value: SortKey; label: string }> = [
  { value: 'impact', label: '重要程度' },
  { value: 'score', label: '评分' },
  { value: 'publish_time', label: '时间' },
];

const RANGE_OPTIONS = [
  { value: 1, label: '近 24 小时' },
  { value: 3, label: '近 3 天' },
  { value: 7, label: '近 7 天' },
];

const SCORE_OPTIONS = [
  { value: 0, label: '全部评分' },
  { value: 4, label: '评分 ≥ 4' },
  { value: 6, label: '评分 ≥ 6' },
  { value: 8, label: '评分 ≥ 8' },
];

function readStoredView(): ViewMode {
  return localStorage.getItem(VIEW_STORAGE_KEY) === 'card' ? 'card' : 'list';
}

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
              <div className="num">{fmtAmount(b.total_amount)}</div>
              <div className="label">成交额(亿)</div>
            </div>
          </>
        )}
      </div>
      {data.headline && (
        <div className="news-reason" style={{ marginTop: 12 }}>
          {data.headline}
        </div>
      )}
    </div>
  );
}

export function NewsFeed() {
  const [src, setSrc] = useState<string | null>(null);
  const [sort, setSort] = useState<SortKey>('impact');
  const [minScore, setMinScore] = useState(0);
  const [days, setDays] = useState(1);
  const [analyzedOnly, setAnalyzedOnly] = useState(false);
  const [view, setView] = useState<ViewMode>(readStoredView);
  const [openId, setOpenId] = useState<string | null>(null);

  // 频道 / 视图切换都要重置到第一页，因此放进 queryKey 让 React Query 自动重新拉取
  const filterKey = { src, sort, minScore, days, analyzedOnly };

  // 渠道计数与列表共用同一组过滤条件（分页除外），保证标签上的数字与列表条数一致
  const baseParams = () => {
    const p = new URLSearchParams({ sort });
    if (src) p.set('source', src);
    if (minScore > 0) p.set('min_score', String(minScore));
    if (analyzedOnly) p.set('has_analysis', 'true');
    p.set('start', new Date(Date.now() - days * 24 * 3600 * 1000).toISOString());
    return p;
  };

  const overview = useQuery({
    queryKey: ['overview'],
    queryFn: () => api.get<MarketOverview>('/market/overview'),
  });

  const sources = useQuery({
    queryKey: ['news-sources', { minScore, days, analyzedOnly }],
    queryFn: () => api.get<NewsSource[]>(`/news/sources?${baseParams()}`),
    staleTime: 60_000,
  });

  const news = useInfiniteQuery({
    queryKey: ['news', filterKey],
    initialPageParam: 1,
    queryFn: ({ pageParam }) => {
      const p = baseParams();
      p.set('page', String(pageParam));
      p.set('page_size', String(PAGE_SIZE));
      return api.get<Page<NewsItem>>(`/news?${p}`);
    },
    getNextPageParam: (last) => (last.has_more ? last.page + 1 : undefined),
  });

  const items = news.data?.pages.flatMap((p) => p.items) ?? [];
  const total = sources.data?.reduce((sum, s) => sum + s.count, 0) ?? 0;

  const changeView = (next: ViewMode) => {
    setView(next);
    localStorage.setItem(VIEW_STORAGE_KEY, next);
  };

  return (
    <div>
      {overview.isLoading ? (
        <Loading />
      ) : overview.error ? (
        <ErrorBox error={overview.error} />
      ) : (
        overview.data && <OverviewCard data={overview.data} />
      )}

      <div className="card">
        <ChannelTabs
          sources={sources.data ?? []}
          value={src}
          total={total}
          onChange={(next) => {
            setSrc(next);
            window.scrollTo({ top: 0, behavior: 'smooth' });
          }}
        />

        <div className="toolbar" style={{ marginTop: 12, marginBottom: 0 }}>
          <select value={sort} onChange={(e) => setSort(e.target.value as SortKey)}>
            {SORT_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                按{o.label}
              </option>
            ))}
          </select>
          <select value={minScore} onChange={(e) => setMinScore(Number(e.target.value))}>
            {SCORE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
          <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
            {RANGE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>

          <label className="row" style={{ gap: 6, cursor: 'pointer', fontSize: 13 }}>
            <input
              type="checkbox"
              checked={analyzedOnly}
              onChange={(e) => setAnalyzedOnly(e.target.checked)}
            />
            仅看已分析
          </label>

          <span className="spacer" />

          <div className="view-toggle">
            <button
              type="button"
              className={view === 'list' ? 'active' : ''}
              onClick={() => changeView('list')}
            >
              列表
            </button>
            <button
              type="button"
              className={view === 'card' ? 'active' : ''}
              onClick={() => changeView('card')}
            >
              卡片
            </button>
          </div>
        </div>
      </div>

      {news.isLoading ? (
        <Loading />
      ) : news.error ? (
        <ErrorBox error={news.error} />
      ) : items.length === 0 ? (
        <div className="card">
          <Empty text="当前筛选条件下暂无资讯（可先运行 cli ingest / score 补充数据）" />
        </div>
      ) : view === 'card' ? (
        <div className="card-grid">
          {items.map((item) => (
            <NewsCard key={item.id} item={item} view="card" onOpen={(i) => setOpenId(i.id)} />
          ))}
        </div>
      ) : (
        <div className="card" style={{ padding: 0 }}>
          {items.map((item) => (
            <NewsCard key={item.id} item={item} view="list" onOpen={(i) => setOpenId(i.id)} />
          ))}
        </div>
      )}

      {news.hasNextPage && (
        <button
          className="load-more"
          disabled={news.isFetchingNextPage}
          onClick={() => news.fetchNextPage()}
        >
          {news.isFetchingNextPage ? '加载中…' : `加载更多（已显示 ${items.length} 条）`}
        </button>
      )}

      <Disclaimer />

      {openId && (
        <NewsDrawer
          newsId={openId}
          fallback={{
            title: items.find((i) => i.id === openId)?.title ?? '',
            analysis_id: items.find((i) => i.id === openId)?.analysis_id ?? null,
          }}
          onClose={() => setOpenId(null)}
          onSelectRelated={setOpenId}
        />
      )}
    </div>
  );
}
