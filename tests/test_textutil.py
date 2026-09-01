"""文本处理：指纹、近似去重、标题兜底。"""
from fin_news.domain.textutil import (
    NEAR_DUP_THRESHOLD,
    content_hash,
    derive_title,
    estimate_tokens,
    hamming_distance,
    normalize_text,
    simhash,
    truncate,
)


def test_content_hash_ignores_punctuation_and_whitespace():
    a = "央行下调存款准备金率0.5个百分点"
    b = "央行下调存款准备金率 0.5 个百分点。"
    assert content_hash(a, "标题") == content_hash(b, "标题")


def test_content_hash_differs_on_content():
    assert content_hash("美联储降息") != content_hash("美联储加息")


def test_normalize_text_collapses_whitespace():
    assert normalize_text(" 央行\n降准  ") == "央行 降准"


def test_simhash_ignores_punctuation_and_whitespace():
    a = "央行决定下调金融机构存款准备金率零点五个百分点，释放长期资金"
    b = "央行决定下调金融机构存款准备金率零点五个百分点， 释放长期资金！"
    assert hamming_distance(simhash(a), simhash(b)) == 0


def test_simhash_ignores_repost_source_prefix():
    """转载时加【财联社X月X日电】前缀应仍判为重复。"""
    base = "央行决定下调金融机构存款准备金率零点五个百分点，释放长期资金"
    repost = "【财联社9月1日电】央行决定下调金融机构存款准备金率零点五个百分点，释放长期资金"
    assert hamming_distance(simhash(base), simhash(repost)) <= NEAR_DUP_THRESHOLD


def test_simhash_detects_truncated_and_slightly_edited_text():
    base = "央行决定下调金融机构存款准备金率零点五个百分点，释放长期资金约一万亿元"
    truncated = "央行决定下调金融机构存款准备金率零点五个百分点"
    edited = "央行决定下调金融机构存贷款利率零点五个百分点，释放长期资金约一万亿元"
    assert hamming_distance(simhash(base), simhash(truncated)) <= NEAR_DUP_THRESHOLD
    assert hamming_distance(simhash(base), simhash(edited)) <= NEAR_DUP_THRESHOLD


def test_simhash_keeps_unrelated_news_apart():
    base = "央行决定下调金融机构存款准备金率零点五个百分点，释放长期资金约一万亿元"
    different = "今日两市成交额突破一万亿元，北向资金净流入，半导体板块领涨两市"
    assert hamming_distance(simhash(base), simhash(different)) > NEAR_DUP_THRESHOLD


def test_derive_title_from_bracket():
    assert derive_title("【基金经理展望下半年科技股】财联社9月1日电，根据已披露的基金半年报") == (
        "基金经理展望下半年科技股"
    )


def test_derive_title_fallback_to_first_sentence():
    title = derive_title("特朗普将于美东时间周二13:30会见油气零售商及炼油厂代表。")
    assert title.startswith("特朗普将于美东时间周二")


def test_derive_title_truncates_long_text():
    long_text = "这是一段非常长的新闻正文内容" * 20
    title = derive_title(long_text, max_len=42)
    assert len(title) == 42
    assert title.endswith("…")


def test_derive_title_handles_empty():
    assert derive_title("") == "(无标题)"


def test_estimate_tokens_positive():
    assert estimate_tokens("") == 0
    assert estimate_tokens("央行降准") > 0


def test_truncate_marks_flag():
    text, truncated = truncate("abcdefghij", 5)
    assert truncated is True
    assert text.endswith("…")
    assert truncate("abc", 5) == ("abc", False)
