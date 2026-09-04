import { useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import type {
  BriefResponse,
  PostMarketBrief as PostMarketBriefData,
  PreMarketBrief as PreMarketBriefData,
} from '../api/types';
import { BriefArchive } from '../components/BriefArchive';
import { PreMarketBrief } from '../components/PreMarketBrief';
import { PostMarketBrief } from '../components/PostMarketBrief';
import { Disclaimer, Empty, ErrorBox, Loading } from '../components/Common';
import { fmtDate, shiftDate } from '../lib/band';

const PERIODS = [
  { value: 'pre_market', label: '盘前展望' },
  { value: 'post_market', label: '盘后复盘' },
];

/** 后端按 period 决定 brief 的具体形态，这里据此收窄联合类型 */
function isPreMarket(
  _brief: PreMarketBriefData | PostMarketBriefData,
  period: string,
): _brief is PreMarketBriefData {
  return period === 'pre_market';
}

export function Reports() {
  // URL query 是页面唯一状态源：可直接分享 / 收藏某一交易日的某一期简报
  const [params, setParams] = useSearchParams();
  const period = params.get('period') === 'post_market' ? 'post_market' : 'pre_market';
  const date = params.get('date') || '';

  const update = (next: { period?: string; date?: string | null }) => {
    const p = new URLSearchParams(params);
    if (next.period !== undefined) p.set('period', next.period);
    if (next.date !== undefined) {
      if (next.date) p.set('date', next.date);
      else p.delete('date');
    }
    setParams(p, { replace: true });
  };

  const query = useQuery({
    queryKey: ['brief', period, date],
    queryFn: () => {
      const p = new URLSearchParams({ period });
      if (date) p.set('date', date);
      return api.get<BriefResponse>(`/market/brief?${p}`);
    },
  });

  const activeDate = query.data?.trade_date ?? date ?? fmtDate(new Date());
  const brief = query.data?.brief;

  return (
    <div>
      <div className="page-head">
        <h2>盘前盘后报告</h2>
        <p>每个交易日的开盘前展望与收盘后复盘，可回溯任意一期历史简报。</p>
      </div>

      <div className="card">
        <div className="tabs">
          {PERIODS.map((p) => (
            <button
              key={p.value}
              type="button"
              className={`tab${period === p.value ? ' active' : ''}`}
              onClick={() => update({ period: p.value })}
            >
              {p.label}
            </button>
          ))}
          <span className="spacer" />
          <button
            className="tab"
            type="button"
            onClick={() => update({ date: shiftDate(activeDate, -1) })}
          >
            ‹ 前一天
          </button>
          <input
            type="date"
            value={activeDate}
            onChange={(e) => e.target.value && update({ date: e.target.value })}
          />
          <button
            className="tab"
            type="button"
            onClick={() => update({ date: shiftDate(activeDate, 1) })}
          >
            后一天 ›
          </button>
        </div>
      </div>

      <div className="reports-layout">
        <div>
          {query.isLoading ? (
            <Loading />
          ) : query.error ? (
            <ErrorBox error={query.error} />
          ) : query.data && !query.data.available ? (
            <div className="card">
              <Empty text={query.data.message || `${activeDate} 暂无该时段简报`} />
            </div>
          ) : !brief ? null : isPreMarket(brief, period) ? (
            <PreMarketBrief data={brief} />
          ) : (
            <PostMarketBrief data={brief} />
          )}
        </div>

        <BriefArchive period={period} activeDate={activeDate} onSelect={(d) => update({ date: d })} />
      </div>

      <Disclaimer />
    </div>
  );
}
