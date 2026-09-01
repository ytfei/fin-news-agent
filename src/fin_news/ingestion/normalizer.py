"""归一化：把数据源原始条目统一为 NormalizedItem（含标题兜底与指纹计算）。"""
from __future__ import annotations

from fin_news.core.enums import IngestKind
from fin_news.domain.schemas import NormalizedItem, RawItem
from fin_news.domain.textutil import content_hash, derive_title, normalize_text, simhash


def normalize(item: RawItem, source_key: str, src: str | None, src_name: str | None) -> NormalizedItem:
    content = normalize_text(item.content)
    title = normalize_text(item.title)

    # wallstreetcn / 部分 cls 快讯的 title 为 None，需要从正文兜底
    derived = False
    if not title:
        title = derive_title(content)
        derived = True

    return NormalizedItem(
        source="tushare",
        source_key=source_key,
        kind=IngestKind.NEWS,
        src=src,
        src_name=src_name,
        external_id=item.external_id,
        title=title,
        content=content,
        content_truncated=False,
        channels=item.channels,
        url=item.url,
        publish_time=item.publish_time,
        content_hash=content_hash(content, title),
        simhash=simhash(f"{title}\n{content}"),
        metadata={
            "title_derived": derived,
            "raw": {k: v for k, v in (item.raw or {}).items() if k in ("channels", "src_site")},
        },
    )


def normalize_batch(
    items: list[RawItem], source_key: str, src: str | None, src_name: str | None
) -> list[NormalizedItem]:
    return [normalize(i, source_key, src, src_name) for i in items]
