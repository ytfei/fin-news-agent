import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import type { BriefMeta } from '../api/types';
import { ErrorBox, Loading } from './Common';

interface Props {
  period: string;
  activeDate: string;
  onSelect: (date: string) => void;
}

/** 历史归档：按交易日列出近期简报，点击切换报告页日期 */
export function BriefArchive({ period, activeDate, onSelect }: Props) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['briefs', period],
    queryFn: () => api.get<BriefMeta[]>(`/market/briefs?days=90&period=${period}`),
  });

  return (
    <div className="archive">
      <h4>历史归档</h4>
      {isLoading ? (
        <Loading />
      ) : error ? (
        <ErrorBox error={error} />
      ) : !data?.length ? (
        <div className="muted">近期暂无{period === 'pre_market' ? '盘前' : '盘后'}简报</div>
      ) : (
        data.map((item) => (
          <button
            key={item.report_id}
            type="button"
            className={`archive-item${item.trade_date === activeDate ? ' active' : ''}`}
            onClick={() => onSelect(item.trade_date)}
          >
            <div className="archive-date">{item.trade_date}</div>
            <div className="archive-title clamp-2">{item.title}</div>
          </button>
        ))
      )}
    </div>
  );
}
