import type { NewsSource } from '../api/types';

interface Props {
  sources: NewsSource[];
  /** 当前选中的渠道标识，null 表示「全部」 */
  value: string | null;
  /** 各渠道条数之和，用于「全部」标签的计数 */
  total: number;
  onChange: (src: string | null) => void;
}

export function ChannelTabs({ sources, value, total, onChange }: Props) {
  return (
    <div className="tabs scroll">
      <button
        type="button"
        className={`tab${value === null ? ' active' : ''}`}
        onClick={() => onChange(null)}
      >
        全部<span className="tab-count">{total}</span>
      </button>
      {sources.map((s) => {
        const src = s.src ?? '';
        return (
          <button
            key={src}
            type="button"
            className={`tab${value === src ? ' active' : ''}`}
            onClick={() => onChange(src)}
          >
            {s.src_name || src}
            <span className="tab-count">{s.count}</span>
          </button>
        );
      })}
      {sources.length === 0 && <span className="muted">暂无渠道数据</span>}
    </div>
  );
}
