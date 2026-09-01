"""文本分块：按估算 token 切分，带重叠，尽量在句读处断开。"""
from __future__ import annotations

import re

from fin_news.domain.textutil import estimate_tokens

_SENT_END_RE = re.compile(r"(?<=[。！？!?；;\n])")


def split_sentences(text: str) -> list[str]:
    parts = _SENT_END_RE.split(text)
    return [p for p in (s.strip() for s in parts) if p]


def chunk_text(
    text: str,
    max_tokens: int = 600,
    overlap_tokens: int = 80,
    prefix: str = "",
) -> list[str]:
    """把长文本切成若干块。

    优先在句读处切分；单句超长时按字符硬切，避免整块丢失。
    """
    text = (text or "").strip()
    if not text:
        return []

    prefix_tokens = estimate_tokens(prefix)
    budget = max(50, max_tokens - prefix_tokens)

    sentences = split_sentences(text)
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0

    def flush() -> None:
        nonlocal current, current_tokens
        if current:
            chunks.append("".join(current))
            current, current_tokens = [], 0

    for sent in sentences:
        st = estimate_tokens(sent)
        if st > budget:
            flush()
            # 超长句按字符硬切
            size = max(50, int(budget * 1.6))
            for i in range(0, len(sent), size):
                chunks.append(sent[i : i + size])
            continue
        if current_tokens + st > budget:
            flush()
            # 重叠：把上一块尾部内容带过来
            if chunks and overlap_tokens > 0:
                tail = chunks[-1]
                tail_chars = int(overlap_tokens * 1.6)
                overlap = tail[-tail_chars:]
                current = [overlap]
                current_tokens = estimate_tokens(overlap)
        current.append(sent)
        current_tokens += st

    flush()
    return [f"{prefix}{c}".strip() for c in chunks if c.strip()]
