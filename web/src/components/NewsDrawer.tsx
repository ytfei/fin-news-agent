import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { api } from '../api/client';
import type { NewsDetail } from '../api/types';
import { BandTag, ScoreBadge } from './ScoreBadge';
import { ErrorBox, Loading } from './Common';
import { fmtTime, sourceLabel } from '../lib/band';

interface Props {
  newsId: string;
  fallback: { title: string; analysis_id?: string | null };
  onClose: () => void;
  /** 从「相关资讯」跳转到另一条资讯：复用当前抽屉，不新开路由 */
  onSelectRelated: (newsId: string) => void;
}

export function NewsDrawer({ newsId, fallback, onClose, onSelectRelated }: Props) {
  const navigate = useNavigate();

  // ESC 关闭：抽屉是覆盖层，Escape 是最符合直觉的退出方式
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const { data, isLoading, error } = useQuery({
    queryKey: ['news-detail', newsId],
    queryFn: () => api.get<NewsDetail>(`/news/${newsId}`),
  });

  const analysisId = data?.analysis_id ?? fallback.analysis_id;

  return (
    <>
      <div className="drawer-mask" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-label="资讯详情">
        <div className="drawer-head">
          <div style={{ flex: 1 }}>
            <h3>{data?.title ?? fallback.title}</h3>
            <div className="source-line">
              {data && <span>{sourceLabel(data)}</span>}
              {data && <span>·</span>}
              <span>{data ? fmtTime(data.publish_time) : '加载中…'}</span>
            </div>
          </div>
          <button className="drawer-close" onClick={onClose} aria-label="关闭">
            ×
          </button>
        </div>

        <div className="drawer-body">
          {isLoading ? (
            <Loading />
          ) : error ? (
            <ErrorBox error={error} />
          ) : !data ? null : (
            <>
              <div className="card">
                <div className="row" style={{ marginBottom: 10 }}>
                  <ScoreBadge score={data.score} band={data.band} />
                  <BandTag band={data.band} />
                  <span className="spacer" />
                  {data.url && (
                    <a href={data.url} target="_blank" rel="noreferrer" className="chip chip-medium">
                      原文
                    </a>
                  )}
                </div>
                {data.score_reason ? (
                  <div className="news-reason">{data.score_reason}</div>
                ) : (
                  <div className="muted">暂无评分依据</div>
                )}
              </div>

              {data.has_analysis && (
                <div className="card">
                  <div className="section-title" style={{ marginTop: 0 }}>
                    AI 分析摘要
                  </div>
                  <div className="news-reason" style={{ background: '#eff6ff', color: '#1e40af' }}>
                    {data.analysis_summary || '该资讯已完成分析，点击查看完整报告。'}
                  </div>
                  <button
                    className="primary"
                    style={{ marginTop: 12 }}
                    disabled={!analysisId}
                    onClick={() => analysisId && navigate(`/analysis/${analysisId}`)}
                  >
                    查看完整分析
                  </button>
                </div>
              )}

              <div className="card">
                <div className="section-title" style={{ marginTop: 0 }}>
                  正文
                </div>
                <div className="article-body">
                  {data.content || '（该资讯无正文内容）'}
                  {data.content_truncated && <span className="muted"> …（原文较长已截断）</span>}
                </div>
              </div>

              {data.score_history.length > 0 && (
                <div className="card">
                  <div className="section-title" style={{ marginTop: 0 }}>
                    评分历史
                  </div>
                  {data.score_history.map((s, i) => (
                    <div className="kv-row" key={i}>
                      <span className="kv-key">{s.score} 分</span>
                      <span className="kv-val">
                        {s.reason || '—'}
                        <div className="muted" style={{ marginTop: 2 }}>
                          {s.model ?? '未知模型'}
                          {s.prompt_version ? ` · ${s.prompt_version}` : ''}
                          {s.created_at ? ` · ${fmtTime(s.created_at)}` : ''}
                        </div>
                      </span>
                    </div>
                  ))}
                </div>
              )}

              {data.related_news.length > 0 && (
                <div className="card">
                  <div className="section-title" style={{ marginTop: 0 }}>
                    相关资讯
                  </div>
                  {data.related_news.map((r) => (
                    <div
                      className="kv-row"
                      key={r.id}
                      style={{ cursor: 'pointer' }}
                      onClick={() => onSelectRelated(r.id)}
                    >
                      <span className="kv-key">{r.score ?? '—'} 分</span>
                      <span className="kv-val">
                        {r.title}
                        <div className="muted" style={{ marginTop: 2 }}>
                          相似度 {(r.similarity * 100).toFixed(0)}%
                        </div>
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      </aside>
    </>
  );
}
