// 轻量 API 客户端：fetch 封装 + SSE 流式

const BASE = '/api/v1';

export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!resp.ok) {
    let detail = `请求失败 (${resp.status})`;
    try {
      const body = await resp.json();
      detail = body.detail || detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(resp.status, detail);
  }
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) }),
  del: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
};

// SSE 流式（追问）：解析后端 `event: xxx / data: {...}` 格式
export async function streamChat(
  path: string,
  body: unknown,
  onDelta: (text: string) => void,
  onEvent: (event: string, data: unknown) => void,
): Promise<void> {
  const resp = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!resp.ok || !resp.body) {
    throw new ApiError(resp.status, `流式请求失败 (${resp.status})`);
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  const processLine = (line: string) => {
    if (line.startsWith('event:')) {
      buffer = line.slice(6).trim();
    } else if (line.startsWith('data:')) {
      const raw = line.slice(5).trim();
      const event = buffer || 'message';
      try {
        const data = JSON.parse(raw);
        if (event === 'delta' && data && typeof data.text === 'string') {
          onDelta(data.text);
        }
        onEvent(event, data);
      } catch {
        /* 非 JSON 数据忽略 */
      }
    }
  };

  let pending = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    pending += decoder.decode(value, { stream: true });
    const lines = pending.split('\n');
    pending = lines.pop() ?? '';
    for (const line of lines) processLine(line.trim());
  }
  if (pending.trim()) processLine(pending.trim());
}

// 设备 ID：MVP 用匿名标识，存 localStorage
export function deviceId(): string {
  let id = localStorage.getItem('fin-news-device-id');
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem('fin-news-device-id', id);
  }
  return id;
}
