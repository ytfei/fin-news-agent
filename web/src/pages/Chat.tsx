import { useEffect, useRef, useState } from 'react';
import { api, streamChat } from '../api/client';
import type { ChatMessage, ChatSession } from '../api/types';
import { Disclaimer, ErrorBox, Loading } from '../components/Common';

interface LocalMsg {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  refs?: Array<Record<string, unknown>>;
}

export function Chat() {
  const [session, setSession] = useState<ChatSession | null>(null);
  const [messages, setMessages] = useState<LocalMsg[]>([]);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const listRef = useRef<HTMLDivElement>(null);

  // 初始化：取最新会话，否则创建
  useEffect(() => {
    (async () => {
      try {
        const sessions = await api.get<{ items: ChatSession[] }>('/chat/sessions?page=1&page_size=1');
        let s = sessions.items[0];
        if (!s) {
          s = await api.post<ChatSession>('/chat/sessions', { title: '新会话' });
        }
        setSession(s);
        const msgs = await api.get<{ items: ChatMessage[] }>(
          `/chat/sessions/${s.id}/messages`,
        );
        setMessages(
          msgs.items
            .filter((m) => m.role === 'user' || m.role === 'assistant')
            .map((m) => ({ id: m.id, role: m.role as 'user' | 'assistant', content: m.content })),
        );
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
  }, [messages]);

  const send = async () => {
    const content = input.trim();
    if (!content || !session || streaming) return;
    setInput('');
    setError(null);
    setMessages((prev) => [...prev, { id: `u-${Date.now()}`, role: 'user', content }]);
    setStreaming(true);

    const assistantId = `a-${Date.now()}`;
    setMessages((prev) => [...prev, { id: assistantId, role: 'assistant', content: '' }]);

    let acc = '';
    let refs: Array<Record<string, unknown>> = [];

    try {
      await streamChat(
        `/chat/sessions/${session.id}/messages`,
        { content, stream: true },
        (delta) => {
          acc += delta;
          setMessages((prev) =>
            prev.map((m) => (m.id === assistantId ? { ...m, content: acc } : m)),
          );
        },
        (_event, data) => {
          const d = data as { items?: Array<Record<string, unknown>> };
          if (d?.items) refs = d.items;
        },
      );
      setMessages((prev) =>
        prev.map((m) => (m.id === assistantId ? { ...m, content: acc, refs } : m)),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setMessages((prev) =>
        prev.map((m) =>
          m.id === assistantId && !m.content ? { ...m, content: '（生成失败，请重试）' } : m,
        ),
      );
    } finally {
      setStreaming(false);
    }
  };

  if (loading) return <Loading />;

  return (
    <div>
      {error && <ErrorBox error={error} />}
      <div className="chat-box">
        <div className="chat-list" ref={listRef}>
          {messages.length === 0 ? (
            <div className="empty">问点什么吧，比如「降准对半导体有什么影响？」</div>
          ) : (
            messages.map((m) => (
              <div key={m.id} className={`msg ${m.role}`}>
                {m.content || (streaming && m.role === 'assistant' ? '…' : '')}
                {m.refs && m.refs.length > 0 && (
                  <div className="muted" style={{ marginTop: 6 }}>
                    引用 {m.refs.length} 条资讯
                  </div>
                )}
              </div>
            ))
          )}
        </div>
        <div className="chat-input-row">
          <input
            placeholder="输入你的问题…"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && send()}
            disabled={streaming}
          />
          <button onClick={send} disabled={streaming || !input.trim()}>
            {streaming ? '生成中' : '发送'}
          </button>
        </div>
      </div>
      <Disclaimer />
    </div>
  );
}
