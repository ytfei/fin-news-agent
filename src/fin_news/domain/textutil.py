"""文本处理工具：归一化、哈希、simhash、标题兜底、token 估算。"""
from __future__ import annotations

import hashlib
import re
import unicodedata

_WS_RE = re.compile(r"\s+")

# 近似去重判定阈值（simhash 汉明距离），取值依据见 simhash() 的实测注释
NEAR_DUP_THRESHOLD = 10
# 计算 content_hash 时忽略的内容：空白 + 常见中英文标点
# 注意：字符类中不要出现"中文字符-中文字符"，会被解析为非法字符区间
_STRIP_RE = re.compile(r"[\s　!-/:-@\[-`{-~，。、《》（）【】“”‘’；：？！、…—·\u3000]+")

_CN_BRACKET_RE = re.compile(r"^【([^】]{4,60})】")
_SENT_SPLIT_RE = re.compile(r"(?<=[。！？!?；;])")


def normalize_text(text: str | None) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    return _WS_RE.sub(" ", text).strip()


def content_hash(content: str, title: str | None = None) -> str:
    """正文指纹：归一化后忽略标点与空白，避免转载/排版差异导致重复入库。"""
    base = f"{title or ''}|{content or ''}"
    squeezed = _STRIP_RE.sub("", base)
    return hashlib.sha256(squeezed.encode("utf-8")).hexdigest()


# ------------------------------ simhash ------------------------------


def _tokens(text: str) -> list[str]:
    """中文按字 bigram，英文/数字按词。"""
    text = text.lower()
    grams: list[str] = []
    buf = ""
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff":
            if buf:
                grams.append(buf)
                buf = ""
            grams.append(ch)
        elif ch.isalnum():
            buf += ch
        else:
            if buf:
                grams.append(buf)
                buf = ""
    if buf:
        grams.append(buf)
    # 中文 bigram
    cn = [c for c in text if "\u4e00" <= c <= "\u9fff"]
    grams += ["".join(cn[i : i + 2]) for i in range(len(cn) - 1)]
    return grams or [text]


def _normalize_for_fingerprint(text: str) -> str:
    """指纹前归一化：去掉转载时常见的来源前缀（如【财联社9月1日电】）。"""
    text = normalize_text(text)
    return _CN_BRACKET_RE.sub("", text).strip()


def simhash(text: str, bits: int = 64) -> int:
    """64 位 simhash，用于近似去重。

    实测校准（中文短资讯，指纹前先去掉【来源】前缀）：
      标点/空格差异 -> 0；加来源前缀 -> 0；截断 -> 9；改 2 字 -> 10；
      无关资讯 -> 25；同题材不同事件 -> 26
    因此去重阈值取 NEAR_DUP_THRESHOLD = 10（与无关资讯有 15 的间隔）。
    """
    vec = [0] * bits
    for token in _tokens(_normalize_for_fingerprint(text)):
        h = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)
        for i in range(bits):
            vec[i] += 1 if (h >> i) & 1 else -1
    fingerprint = 0
    for i in range(bits):
        if vec[i] > 0:
            fingerprint |= 1 << i
    return fingerprint - (1 << 63) if fingerprint >= (1 << 63) else fingerprint


def hamming_distance(a: int, b: int) -> int:
    return bin((a ^ b) & ((1 << 64) - 1)).count("1")


# ------------------------------ 标题兜底 ------------------------------


def derive_title(content: str | None, max_len: int = 42) -> str:
    """Tushare 的 title 字段常为 None（尤其 wallstreetcn），从正文兜底生成标题。"""
    text = normalize_text(content)
    if not text:
        return "(无标题)"

    m = _CN_BRACKET_RE.match(text)
    if m:
        return m.group(1).strip()

    first = _SENT_SPLIT_RE.split(text)[0].strip()
    if not first:
        first = text
    if len(first) > max_len:
        return first[: max_len - 1] + "…"
    return first


def estimate_tokens(text: str) -> int:
    """粗略估算：中文约 1 字 0.7 token，英文约 4 字符 1 token。"""
    if not text:
        return 0
    cn = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    other = len(text) - cn
    return int(cn * 0.75 + other / 3.5) + 1


def truncate(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars] + "…", True
