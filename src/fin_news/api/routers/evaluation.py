"""评估集接口（P6）：抽样 → 人工抽查分档 → 一致率统计。

用途：量化「模型分档 vs 人工分档」的一致率，对应 PRD §8 MVP 验收标准第 2 条
「人工抽 100 条，分档一致率 ≥ 80%」。

一致率口径：
* `exact_rate`：人工分与模型分**完全相等**的比例（严格）
* `band_agree_rate`：人工分档与模型分档**相同**的比例（**验收口径**）
* `mean_abs_diff`：分数平均绝对误差
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from fin_news.api.deps import PaginationDep, SessionDep
from fin_news.api.errors import BadRequestError, NotFoundError
from fin_news.core.enums import ScoreBand
from fin_news.core.logging import get_logger
from fin_news.core.timeutil import now_utc
from fin_news.domain.scoring import band_for_score
from fin_news.models.evaluation import ScoreEvalLabel, ScoreEvalSet
from fin_news.models.news import NewsItem

logger = get_logger("api.evaluation")

router = APIRouter(prefix="/eval-sets", tags=["evaluation"])

# 抽样策略
STRATEGY_STRATIFIED = "stratified_band"  # 按分档分层（默认，保证各档都有样本）
STRATEGY_RANDOM = "random"
STRATEGY_LATEST = "latest"


# ----------------------------------------------------------------------
# 请求 / 响应模型
# ----------------------------------------------------------------------
class EvalSetCreate(BaseModel):
    name: str = Field(..., max_length=128)
    description: str = ""
    sample_size: int = Field(100, ge=1, le=2000)
    strategy: str = Field(STRATEGY_STRATIFIED, description="stratified_band | random | latest")
    filters: dict = Field(default_factory=dict, description="{start, end, sources, min_score, max_score}")


class EvalLabelSubmit(BaseModel):
    human_score: int = Field(..., ge=1, le=10)
    human_note: str = ""
    labeled_by: str | None = None


class EvalSetOut(BaseModel):
    id: int
    public_id: str
    name: str
    description: str
    status: str
    strategy: str
    sample_size: int
    total_items: int
    labeled_items: int
    exact_rate: float | None = None
    band_agree_rate: float | None = None
    mean_abs_diff: float | None = None
    created_at: datetime | None = None


# ----------------------------------------------------------------------
# 抽样
# ----------------------------------------------------------------------
def _apply_filters(stmt, filters: dict):
    """把抽样过滤条件应用到 NewsItem 查询。"""
    if filters.get("start"):
        stmt = stmt.where(NewsItem.publish_time >= filters["start"])
    if filters.get("end"):
        stmt = stmt.where(NewsItem.publish_time <= filters["end"])
    if filters.get("sources"):
        stmt = stmt.where(NewsItem.src.in_(filters["sources"]))
    if filters.get("min_score") is not None:
        stmt = stmt.where(NewsItem.score >= filters["min_score"])
    if filters.get("max_score") is not None:
        stmt = stmt.where(NewsItem.score <= filters["max_score"])
    return stmt


async def _sample_news(session, sample_size: int, strategy: str, filters: dict) -> list[NewsItem]:
    """按策略抽取已评分资讯（score 非空才能评估）。"""
    base = _apply_filters(select(NewsItem).where(NewsItem.score.is_not(None)), filters)

    if strategy == STRATEGY_RANDOM:
        rows = await session.execute(base.order_by(func.random()).limit(sample_size))
        return list(rows.scalars().all())

    if strategy == STRATEGY_LATEST:
        rows = await session.execute(
            base.order_by(NewsItem.publish_time.desc()).limit(sample_size)
        )
        return list(rows.scalars().all())

    # 默认：按 band 分层，每层平均分配名额，不足则取该层全部
    count_stmt = _apply_filters(
        select(NewsItem.band, func.count()).where(NewsItem.score.is_not(None)), filters
    ).group_by(NewsItem.band)
    counts = {band: cnt for band, cnt in (await session.execute(count_stmt)).all()}
    if not counts:
        return []

    per_band = max(1, sample_size // len(counts))
    items: list[NewsItem] = []
    seen: set[int] = set()
    for band in counts:
        rows = await session.execute(
            _apply_filters(
                select(NewsItem).where(NewsItem.score.is_not(None), NewsItem.band == band), filters
            )
            .order_by(func.random())
            .limit(per_band)
        )
        for item in rows.scalars().all():
            if item.id in seen:
                continue
            seen.add(item.id)
            items.append(item)
            if len(items) >= sample_size:
                return items
    return items


# ----------------------------------------------------------------------
# 统计
# ----------------------------------------------------------------------
def compute_stats(labels: list[ScoreEvalLabel]) -> dict:
    """由标注样本计算一致率指标。"""
    labeled = [
        label
        for label in labels
        if label.human_score is not None and label.model_score is not None
    ]
    if not labeled:
        return {
            "exact_rate": None,
            "band_agree_rate": None,
            "mean_abs_diff": None,
            "confusion": {},
            "band_stats": {},
        }

    total = len(labeled)
    exact = sum(1 for label in labeled if label.human_score == label.model_score)
    band_agree = sum(1 for label in labeled if label.human_band == label.model_band)
    mean_abs_diff = sum(abs(label.human_score - label.model_score) for label in labeled) / total

    # 混淆矩阵：model_band -> human_band -> count
    confusion: dict[str, dict[str, int]] = {}
    for label in labeled:
        confusion.setdefault(label.model_band, {})
        confusion[label.model_band][label.human_band] = (
            confusion[label.model_band].get(label.human_band, 0) + 1
        )

    # 每个模型分档的一致率
    band_stats: dict[str, dict] = {}
    for band in {label.model_band for label in labeled}:
        subset = [label for label in labeled if label.model_band == band]
        agree = sum(1 for label in subset if label.human_band == label.model_band)
        band_stats[band] = {
            "total": len(subset),
            "agree": agree,
            "rate": round(agree / len(subset), 4),
        }

    return {
        "exact_rate": round(exact / total, 4),
        "band_agree_rate": round(band_agree / total, 4),
        "mean_abs_diff": round(mean_abs_diff, 4),
        "confusion": confusion,
        "band_stats": band_stats,
    }


async def _refresh_stats(session, eval_set: ScoreEvalSet) -> None:
    """重算并回写评估集统计。"""
    labels = (
        await session.execute(
            select(ScoreEvalLabel).where(ScoreEvalLabel.eval_set_id == eval_set.id)
        )
    ).scalars().all()

    stats = compute_stats(labels)
    eval_set.exact_rate = stats["exact_rate"]
    eval_set.band_agree_rate = stats["band_agree_rate"]
    eval_set.mean_abs_diff = stats["mean_abs_diff"]
    eval_set.confusion = stats["confusion"]
    eval_set.band_stats = stats["band_stats"]
    eval_set.labeled_items = sum(1 for label in labels if label.human_score is not None)
    eval_set.total_items = len(labels)

    if eval_set.total_items and eval_set.labeled_items >= eval_set.total_items:
        eval_set.status = "DONE"
        eval_set.completed_at = now_utc()
    elif eval_set.labeled_items:
        eval_set.status = "IN_PROGRESS"

    await session.flush()


async def _get_eval_set(session, eval_set_id: int) -> ScoreEvalSet:
    eval_set = (
        await session.execute(
            select(ScoreEvalSet).where(ScoreEvalSet.id == eval_set_id)
        )
    ).scalar_one_or_none()
    if eval_set is None:
        raise NotFoundError(f"评估集 {eval_set_id} 不存在")
    return eval_set


# ----------------------------------------------------------------------
# 接口
# ----------------------------------------------------------------------
@router.post("", summary="创建评估集（抽样）", status_code=201)
async def create_eval_set(session: SessionDep, payload: EvalSetCreate):
    """按策略从已评分资讯中抽样，建立评估集并快照模型评分。"""
    items = await _sample_news(session, payload.sample_size, payload.strategy, payload.filters)
    if not items:
        raise BadRequestError("没有符合抽样条件的已评分资讯（请放宽过滤条件或先跑评分）")

    eval_set = ScoreEvalSet(
        name=payload.name,
        description=payload.description,
        status="DRAFT",
        strategy=payload.strategy,
        sample_size=payload.sample_size,
        filters=payload.filters,
        total_items=len(items),
        labeled_items=0,
    )
    session.add(eval_set)
    await session.flush()

    for item in items:
        session.add(
            ScoreEvalLabel(
                eval_set_id=eval_set.id,
                news_id=item.id,
                model_score=item.score,
                model_band=item.band.value if item.band else None,
                model_reason=item.score_reason,
                model_version=item.score_version,
            )
        )
    await session.flush()

    logger.info(
        "评估集已创建",
        eval_set_id=eval_set.id,
        name=payload.name,
        strategy=payload.strategy,
        samples=len(items),
    )
    return {
        "id": eval_set.id,
        "public_id": str(eval_set.public_id),
        "name": eval_set.name,
        "total_items": eval_set.total_items,
        "strategy": eval_set.strategy,
    }


@router.get("", summary="评估集列表")
async def list_eval_sets(session: SessionDep, pagination: PaginationDep):
    total = await session.scalar(select(func.count()).select_from(ScoreEvalSet))
    rows = (
        await session.execute(
            select(ScoreEvalSet)
            .order_by(ScoreEvalSet.created_at.desc())
            .offset(pagination.offset)
            .limit(pagination.page_size)
        )
    ).scalars().all()

    return {
        "page": pagination.page,
        "page_size": pagination.page_size,
        "total": int(total or 0),
        "has_more": (pagination.offset + len(rows)) < int(total or 0),
        "items": [
            {
                "id": item.id,
                "public_id": str(item.public_id),
                "name": item.name,
                "status": item.status,
                "strategy": item.strategy,
                "total_items": item.total_items,
                "labeled_items": item.labeled_items,
                "exact_rate": item.exact_rate,
                "band_agree_rate": item.band_agree_rate,
                "mean_abs_diff": item.mean_abs_diff,
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
            for item in rows
        ],
    }


@router.get("/{eval_set_id}", summary="评估集详情与统计")
async def get_eval_set(eval_set_id: int, session: SessionDep):
    eval_set = await _get_eval_set(session, eval_set_id)
    return {
        "id": eval_set.id,
        "public_id": str(eval_set.public_id),
        "name": eval_set.name,
        "description": eval_set.description,
        "status": eval_set.status,
        "strategy": eval_set.strategy,
        "sample_size": eval_set.sample_size,
        "filters": eval_set.filters,
        "total_items": eval_set.total_items,
        "labeled_items": eval_set.labeled_items,
        "exact_rate": eval_set.exact_rate,
        "band_agree_rate": eval_set.band_agree_rate,
        "mean_abs_diff": eval_set.mean_abs_diff,
        "confusion": eval_set.confusion,
        "band_stats": eval_set.band_stats,
        "created_at": eval_set.created_at.isoformat() if eval_set.created_at else None,
        "completed_at": eval_set.completed_at.isoformat() if eval_set.completed_at else None,
    }


@router.get("/{eval_set_id}/labels", summary="评估样本列表")
async def list_labels(
    eval_set_id: int,
    session: SessionDep,
    pagination: PaginationDep,
    only_unlabeled: bool = False,
    band: str | None = None,
):
    """样本列表（含资讯标题/正文摘要与模型评分，供人工分档界面展示）。"""
    await _get_eval_set(session, eval_set_id)

    stmt = select(ScoreEvalLabel).where(ScoreEvalLabel.eval_set_id == eval_set_id)
    if only_unlabeled:
        stmt = stmt.where(ScoreEvalLabel.human_score.is_(None))
    if band:
        stmt = stmt.where(ScoreEvalLabel.model_band == band)
    stmt = stmt.order_by(ScoreEvalLabel.id).offset(pagination.offset).limit(pagination.page_size)

    labels = (await session.execute(stmt)).scalars().all()

    # 批量取资讯正文，避免逐条查询
    news_ids = [label.news_id for label in labels]
    news_map: dict[int, NewsItem] = {}
    if news_ids:
        rows = await session.execute(select(NewsItem).where(NewsItem.id.in_(news_ids)))
        news_map = {item.id: item for item in rows.scalars().all()}

    items = []
    for label in labels:
        news = news_map.get(label.news_id)
        items.append(
            {
                "id": label.id,
                "news_id": label.news_id,
                "title": news.title if news else "(资讯已删除)",
                "content": (news.content or "")[:600] if news else "",
                "src_name": news.src_name if news else None,
                "publish_time": (
                    news.publish_time.isoformat() if news and news.publish_time else None
                ),
                "model_score": label.model_score,
                "model_band": label.model_band,
                "model_reason": label.model_reason,
                "human_score": label.human_score,
                "human_band": label.human_band,
                "human_note": label.human_note,
                "is_agree": label.is_agree,
            }
        )

    return {
        "page": pagination.page,
        "page_size": pagination.page_size,
        "items": items,
        "bands": [band.value for band in ScoreBand],
    }


@router.post("/{eval_set_id}/labels/{label_id}", summary="提交人工分档")
async def submit_label(
    eval_set_id: int, label_id: int, session: SessionDep, payload: EvalLabelSubmit
):
    """提交人工评分，自动计算分档与是否与模型一致，并刷新评估集统计。"""
    await _get_eval_set(session, eval_set_id)
    label = (
        await session.execute(
            select(ScoreEvalLabel).where(
                ScoreEvalLabel.id == label_id, ScoreEvalLabel.eval_set_id == eval_set_id
            )
        )
    ).scalar_one_or_none()
    if label is None:
        raise NotFoundError(f"评估样本 {label_id} 不存在")

    human_band = band_for_score(payload.human_score)
    label.human_score = payload.human_score
    label.human_band = human_band.value
    label.human_note = payload.human_note or None
    label.labeled_by = payload.labeled_by
    label.labeled_at = now_utc()
    label.is_agree = label.model_band == human_band.value
    await session.flush()

    eval_set = await _get_eval_set(session, eval_set_id)
    await _refresh_stats(session, eval_set)

    logger.info(
        "评估标注已提交",
        eval_set_id=eval_set_id,
        label_id=label_id,
        human_score=payload.human_score,
        human_band=human_band.value,
        agree=label.is_agree,
    )
    return {
        "id": label.id,
        "human_score": label.human_score,
        "human_band": label.human_band,
        "is_agree": label.is_agree,
        "labeled_items": eval_set.labeled_items,
        "total_items": eval_set.total_items,
        "band_agree_rate": eval_set.band_agree_rate,
    }


@router.delete("/{eval_set_id}", summary="删除评估集", status_code=204)
async def delete_eval_set(eval_set_id: int, session: SessionDep):
    eval_set = await _get_eval_set(session, eval_set_id)
    await session.delete(eval_set)
    await session.flush()
    logger.info("评估集已删除", eval_set_id=eval_set_id)
    return None
