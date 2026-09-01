"""规则层噪声过滤：廉价前置过滤，减少后续 LLM 评分成本。

注意：这里只过滤「确定无价值」的内容（广告、空内容、荐股话术），
市场相关性的判断交给评分 Agent，避免规则误杀。
"""
from __future__ import annotations

import re

from fin_news.domain.schemas import NormalizedItem

# 广告 / 荐股 / 引流
_AD_PATTERNS = [
    r"加(我|微信|QQ|群)",
    r"扫码|二维码|长按识别",
    r"领取牛股|免费诊股|牛股推荐|内幕消息",
    r"请联系|咨询热线|开户链接",
    r"广告|推广|商务合作",
    r"点击(订阅|关注)领取",
]
_AD_RE = re.compile("|".join(_AD_PATTERNS))

MIN_CONTENT_LEN = 4


def filter_reason(item: NormalizedItem) -> str | None:
    """返回过滤原因；None 表示保留。"""
    text = f"{item.title} {item.content}".strip()
    if len(text.replace(" ", "")) < MIN_CONTENT_LEN:
        return "content_too_short"
    if _AD_RE.search(text):
        return "advertisement"
    return None


def is_noise(item: NormalizedItem) -> bool:
    return filter_reason(item) is not None
