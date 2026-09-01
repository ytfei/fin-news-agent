export function Disclaimer() {
  return <div className="disclaimer">AI 生成，仅供参考，不构成投资建议。</div>;
}

export function Loading() {
  return <div className="loading">加载中…</div>;
}

export function Empty({ text = '暂无数据' }: { text?: string }) {
  return <div className="empty">{text}</div>;
}

export function ErrorBox({ error }: { error: unknown }) {
  const msg = error instanceof Error ? error.message : String(error);
  return <div className="error-box">出错了：{msg}</div>;
}
