"""文本分块策略。"""
from fin_news.domain.chunking import chunk_text
from fin_news.domain.textutil import estimate_tokens

LONG_TEXT = "".join(f"第{i}段内容，包含对市场的描述与分析结论。" for i in range(200))


def test_empty_input_yields_no_chunks():
    assert chunk_text("") == []
    assert chunk_text(None) == []


def test_short_text_single_chunk():
    chunks = chunk_text("央行宣布降准0.5个百分点。", max_tokens=600)
    assert len(chunks) == 1


def test_long_text_is_split():
    chunks = chunk_text(LONG_TEXT, max_tokens=200, overlap_tokens=40)
    assert len(chunks) > 3


def test_prefix_attached_to_every_chunk():
    prefix = "【财联社】2026-09-01 标题\n"
    chunks = chunk_text(LONG_TEXT, max_tokens=200, overlap_tokens=40, prefix=prefix)
    assert all(c.startswith(prefix) for c in chunks)


def test_chunk_size_respects_budget():
    max_tokens = 200
    chunks = chunk_text(LONG_TEXT, max_tokens=max_tokens, overlap_tokens=0)
    # 允许句读切分带来的少量溢出，但不应该出现超大块
    assert all(estimate_tokens(c) <= max_tokens * 2 for c in chunks)


def test_overlap_is_applied():
    chunks = chunk_text(LONG_TEXT, max_tokens=150, overlap_tokens=50)
    assert len(chunks) > 2
    # 相邻块之间存在重叠文本
    assert chunks[1][:20] in chunks[0]
