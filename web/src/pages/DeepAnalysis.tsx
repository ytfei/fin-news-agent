import { useState } from 'react';
import { useInfiniteQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import type { DeepAnalysisItem, Page } from '../api/types';
import { AnalysisCard } from '../components/AnalysisCard';
import { Disclaimer, Empty, ErrorBox, Loading } from '../components/Common';

type SortKey = 'published_at' | 'score' | 'impact';

const PAGE_SIZE = 20;

const TYPE_TABS = [
  { value: '', label: '全部' },
  { value: 'macro_policy', label: '宏观' },
  { value: 'industry', label: '行业' },
  { value: 'stock', label: '个股' },
];

const SORT_OPTIONS: Array<{ value: SortKey; label: string }> = [
  { value: 'published_at', label: '最新' },
  { value: 'impact', label: '重要程度' },
  { value: 'score', label: '评分' },
];

const RANGE_OPTIONS = [
  { value: 0, label: '全部时间' },
  { value: 3, label: '近 3 天' },
  { value: 7, label: '近 7 天' },
  { value: 30, label: '近 30 天' },
];

export function DeepAnalysis() {
  const [agentType, setAgentType] = useState('');
  const [sort, setSort] = useState<SortKey>('published_at');
  const [days, setDays] = useState(0);

  const query = useInfiniteQuery({
    queryKey: ['deep-analysis', { agentType, sort, days }],
    initialPageParam: 1,
    queryFn: ({ pageParam }) => {
      const p = new URLSearchParams({
        sort,
        page: String(pageParam),
        page_size: String(PAGE_SIZE),
      });
      if (agentType) p.set('agent_type', agentType);
      if (days > 0) {
        p.set('start', new Date(Date.now() - days * 24 * 3600 * 1000).toISOString());
      }
      return api.get<Page<DeepAnalysisItem>>(`/analysis/deep?${p}`);
    },
    getNextPageParam: (last) => (last.has_more ? last.page + 1 : undefined),
  });

  const items = query.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <div>
      <div className="page-head">
        <h2>深度分析</h2>
        <p>评分 3 分以上、且 AI 已完成详细分析的资讯，按宏观政策 / 行业 / 个股三类 Agent 产出。</p>
      </div>

      <div className="card">
        <div className="tabs">
          {TYPE_TABS.map((t) => (
            <button
              key={t.value}
              type="button"
              className={`tab${agentType === t.value ? ' active' : ''}`}
              onClick={() => setAgentType(t.value)}
            >
              {t.label}
            </button>
          ))}
          <span className="spacer" />
          <select value={sort} onChange={(e) => setSort(e.target.value as SortKey)}>
            {SORT_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                按{o.label}
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
        </div>
      </div>

      {query.isLoading ? (
        <Loading />
      ) : query.error ? (
        <ErrorBox error={query.error} />
      ) : items.length === 0 ? (
        <div className="card">
          <Empty text="暂无符合条件的深度分析（资讯需先完成评分与向量化，才会进入深度分析链路）" />
        </div>
      ) : (
        <>
          <div className="card-grid">
            {items.map((item) => (
              <AnalysisCard key={item.id} item={item} />
            ))}
          </div>
          {query.hasNextPage && (
            <button
              className="load-more"
              disabled={query.isFetchingNextPage}
              onClick={() => query.fetchNextPage()}
            >
              {query.isFetchingNextPage ? '加载中…' : `加载更多（已显示 ${items.length} 条）`}
            </button>
          )}
        </>
      )}

      <Disclaimer />
    </div>
  );
}
