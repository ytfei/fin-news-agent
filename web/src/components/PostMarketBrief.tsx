import type { PostMarketBrief as PostMarketBriefData } from '../api/types';
import { fmtAmount } from '../lib/band';

const STATE_LABEL: Record<string, { text: string; cls: string }> = {
  up: { text: '上涨', cls: 'up' },
  down: { text: '下跌', cls: 'down' },
  flat: { text: '平盘', cls: 'flat' },
  volatile: { text: '震荡', cls: 'flat' },
};

/** 盘后复盘正文：一句话结论 + 市场统计 + 涨跌归因 + 次日关注 */
export function PostMarketBrief({ data }: { data: PostMarketBriefData }) {
  const s = (v: unknown) => (v == null ? '—' : String(v));
  const verdict = data.verdict ?? {};
  const state = String(verdict.state ?? 'flat');
  const st = STATE_LABEL[state] ?? STATE_LABEL.flat;
  const marketStats = (data.content?.extras as Record<string, unknown> | undefined)?.market_stats as
    | Record<string, unknown>
    | undefined;

  return (
    <>
      <div className="card">
        <h3 className="card-title">盘后复盘 · {data.trade_date ?? ''}</h3>
        <h2 style={{ margin: '8px 0', fontSize: 19, fontWeight: 600 }}>{data.title}</h2>
        {data.summary && (
          <p style={{ color: '#4b5563', lineHeight: 1.75, margin: 0 }}>{data.summary}</p>
        )}

        {verdict.one_liner != null && (
          <div className="news-reason" style={{ fontSize: 14, padding: 14, marginTop: 12 }}>
            <strong className={st.cls}>[{st.text}]</strong> {String(verdict.one_liner)}
          </div>
        )}

        {marketStats && (
          <div className="grid" style={{ marginTop: 14 }}>
            <div className="stat">
              <div className="num up">{s(marketStats.advance)}</div>
              <div className="label">上涨</div>
            </div>
            <div className="stat">
              <div className="num down">{s(marketStats.decline)}</div>
              <div className="label">下跌</div>
            </div>
            <div className="stat">
              <div className="num up">{s(marketStats.limit_up)}</div>
              <div className="label">涨停</div>
            </div>
            <div className="stat">
              <div className="num down">{s(marketStats.limit_down)}</div>
              <div className="label">跌停</div>
            </div>
            <div className="stat">
              <div className="num">{fmtAmount(marketStats.total_amount as number | string | null)}</div>
              <div className="label">成交额(亿)</div>
            </div>
          </div>
        )}
      </div>

      {data.attribution.length > 0 && (
        <div className="card">
          <h3 className="card-title">涨跌归因（按贡献度）</h3>
          {data.attribution.map((attr, i) => {
            const dir = String(attr.direction ?? '');
            const weight = Number(attr.weight ?? 0);
            return (
              <div className="attribution" key={i}>
                <div className="weight-bar">
                  <span
                    style={{
                      width: `${Math.max(8, weight * 100)}%`,
                      background: dir === 'negative' ? 'var(--down)' : 'var(--up)',
                    }}
                  />
                </div>
                <div style={{ flex: 1 }}>
                  <div style={{ fontWeight: 500 }}>{String(attr.factor ?? '')}</div>
                  <div className="muted" style={{ marginTop: 4 }}>
                    {dir === 'negative' ? '利空' : '利好'} · 权重 {(weight * 100).toFixed(0)}%
                    {Array.isArray(attr.news_ids) && attr.news_ids.length > 0 && (
                      <> · 引用 {attr.news_ids.length} 条资讯</>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {data.next_day_focus.length > 0 && (
        <div className="card">
          <h3 className="card-title">次日关注</h3>
          <ul style={{ margin: 0, paddingLeft: 20, lineHeight: 1.9 }}>
            {data.next_day_focus.map((item, i) => (
              <li key={i}>{item}</li>
            ))}
          </ul>
        </div>
      )}
    </>
  );
}
