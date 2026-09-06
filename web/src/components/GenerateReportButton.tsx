import { useState } from 'react';
import { api } from '../api/client';

type Phase = 'idle' | 'loading' | 'queued' | 'error';

interface Props {
  newsId: string;
  /** 是否已过期（过期资讯同样可手动触发，仅文案不变） */
  block?: boolean;
}

/**
 * 「生成报告」按钮：手动触发深度分析（POST /news/{id}/analyze）。
 *
 * 后端返回 202 只代表「已入队 / 已插队」，实际分析需 1~6 分钟，因此成功后
 * 进入「已加入队列」态并提示用户稍后刷新，而非等待结果。
 */
export function GenerateReportButton({ newsId, block = false }: Props) {
  const [phase, setPhase] = useState<Phase>('idle');

  const onClick = async (e: React.MouseEvent) => {
    // 阻止冒泡：按钮嵌在卡片内部，避免同时触发卡片的 onOpen
    e.stopPropagation();
    if (phase === 'loading' || phase === 'queued') return;
    setPhase('loading');
    try {
      await api.post(`/news/${newsId}/analyze`);
      setPhase('queued');
    } catch (err) {
      console.error('生成报告请求失败', err);
      setPhase('error');
    }
  };

  const label = phase === 'loading' ? '提交中…' : phase === 'queued' ? '已加入队列' : phase === 'error' ? '重试' : '生成报告';
  const title =
    phase === 'queued'
      ? '已加入队列，正在生成，稍后刷新即可查看'
      : phase === 'error'
        ? '请求失败，点击重试'
        : '手动触发生成分析报告';

  return (
    <button
      type="button"
      className={`btn-outline ${block ? 'btn-block' : ''}`}
      disabled={phase === 'loading' || phase === 'queued'}
      onClick={onClick}
      title={title}
    >
      {label}
    </button>
  );
}
