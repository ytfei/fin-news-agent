import type { PreMarketBrief as PreMarketBriefData } from '../api/types';
import { fmtPct, pctClass } from '../lib/band';

/** 盘前展望正文：标题摘要 + 隔夜美股行情网格 + 今日关注方向 */
export function PreMarketBrief({ data }: { data: PreMarketBriefData }) {
  return (
    <>
      <div className="card">
        <h3 className="card-title">盘前展望 · {data.trade_date ?? ''}</h3>
        <h2 style={{ margin: '8px 0', fontSize: 19, fontWeight: 600 }}>{data.title}</h2>
        {data.summary && (
          <p style={{ color: '#4b5563', lineHeight: 1.75, margin: 0 }}>{data.summary}</p>
        )}
      </div>

      {data.us_market.length > 0 && (
        <div className="card">
          <h3 className="card-title">隔夜美股</h3>
          <div className="grid">
            {data.us_market.map((u, i) => {
              const symbol = String(u.symbol ?? '');
              const name = String(u.name ?? symbol);
              const pct = typeof u.pct_chg === 'number' ? u.pct_chg : null;
              const close = typeof u.close === 'number' ? u.close : null;
              return (
                <div className="stat" key={i}>
                  <div className="num muted" style={{ fontSize: 12 }} title={symbol}>
                    {name || symbol}
                  </div>
                  <div className={`num ${pctClass(pct)}`}>{fmtPct(pct)}</div>
                  {close != null && <div className="label">{close.toFixed(2)}</div>}
                </div>
              );
            })}
          </div>
          {data.us_market[0] && (data.us_market[0] as Record<string, unknown>).source === 'news' && (
            <div className="muted" style={{ marginTop: 8 }}>
              注：隔夜美股数据当前无行情接口权限，以上来自相关资讯（降级方案）。
            </div>
          )}
        </div>
      )}

      {data.focus_directions.length > 0 && (
        <div className="card">
          <h3 className="card-title">今日关注方向</h3>
          {data.focus_directions.map((d, i) => (
            <div className="attribution" key={i}>
              <div style={{ flex: 1 }}>
                <div style={{ fontWeight: 500 }}>{String(d.direction ?? d.title ?? '')}</div>
                {d.reason != null && (
                  <div className="muted" style={{ marginTop: 4, lineHeight: 1.6 }}>
                    {String(d.reason)}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
