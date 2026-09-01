"""评分图的纯节点单测（不依赖真实模型）。"""
import pytest

from fin_news.agents.graphs.scoring_graph import (
    MAX_RESCUE_ROUNDS,
    ScoringRun,
    _is_degenerate,
    _quality,
    build_payload,
    rescue_node,
    validate_node,
)
from fin_news.agents.schemas import ScoreBatchModel, ScoreEntityModel, ScoreItemModel
from fin_news.core.enums import ScoreBand
from fin_news.domain.scoring import band_for_score


class _FakeNews:
    """只需要 id / title / content / publish_time / src / src_name 的替身。"""

    def __init__(self, news_id: int, title: str = "标题", content: str = "正文"):
        self.id = news_id
        self.title = title
        self.content = content
        self.publish_time = None
        self.src = "cls"
        self.src_name = "财联社"


def _batch(*pairs) -> ScoreBatchModel:
    return ScoreBatchModel(
        items=[ScoreItemModel(id=i, score=s, reason=f"理由{i}") for i, s in pairs]
    )


# ------------------------------ 提示构建 ------------------------------


def test_build_payload_numbers_from_one():
    payload = build_payload([_FakeNews(10), _FakeNews(20)], max_chars=100)
    assert "1. " in payload and "2. " in payload


def test_build_payload_is_empty_for_no_items():
    # 空列表也要能构造（不会走到模型，但不应抛异常）
    assert "0" in build_payload([], max_chars=100)


def test_build_payload_truncates_long_content():
    payload = build_payload([_FakeNews(1, content="长" * 5000)], max_chars=50)
    assert "长" * 5000 not in payload


# ------------------------------ validate 节点 ------------------------------


def test_validate_maps_index_to_news_id():
    pending = [_FakeNews(101), _FakeNews(102)]
    out = validate_node({"pending": pending, "raw": _batch((1, 9), (2, 2)), "scored": {}})
    assert out["scored"][101].score == 9
    assert out["scored"][102].score == 2
    assert out["missing"] == []


def test_validate_drops_out_of_range_and_duplicate_ids():
    pending = [_FakeNews(1)]
    raw = _batch((1, 6), (1, 7), (99, 8), (0, 5), (-3, 4))
    out = validate_node({"pending": pending, "raw": raw, "scored": {}})
    assert list(out["scored"]) == [1]
    assert out["scored"][1].score == 6  # 重复编号只保留第一条


def test_validate_clamps_scores():
    pending = [_FakeNews(1), _FakeNews(2)]
    out = validate_node({"pending": pending, "raw": _batch((1, 100), (2, -5)), "scored": {}})
    assert out["scored"][1].score == 10
    assert out["scored"][2].score == 1


def test_validate_accumulates_across_rounds():
    pending = [_FakeNews(1), _FakeNews(2)]
    first = validate_node({"pending": pending, "raw": _batch((1, 8)), "scored": {}})
    assert [n.id for n in first["missing"]] == [2]

    # 第二轮：pending 已被 rescue 换成漏评条目，编号重新从 1 开始
    second = validate_node(
        {"pending": first["missing"], "raw": _batch((1, 5)), "scored": first["scored"]}
    )
    assert set(second["scored"]) == {1, 2}
    assert second["scored"][2].score == 5
    assert second["missing"] == []


def test_validate_no_raw_yields_all_missing():
    pending = [_FakeNews(1), _FakeNews(2)]
    out = validate_node({"pending": pending, "raw": None, "scored": {}})
    assert [n.id for n in out["missing"]] == [1, 2]
    assert out["scored"] == {}


def test_validate_flags_suspect_when_scores_are_uniform():
    pending = [_FakeNews(i) for i in range(1, 11)]
    raw = _batch(*[(i, 10) for i in range(1, 11)])
    out = validate_node({"pending": pending, "raw": raw, "scored": {}})
    assert out["suspect"] is True


def test_validate_not_suspect_when_scores_vary():
    pending = [_FakeNews(i) for i in range(1, 11)]
    raw = _batch(*[(i, (i % 10) + 1) for i in range(1, 11)])
    out = validate_node({"pending": pending, "raw": raw, "scored": {}})
    assert out["suspect"] is False


def test_validate_keeps_tags_and_entities():
    raw = ScoreBatchModel(
        items=[
            ScoreItemModel(
                id=1,
                score=6,
                tags=["光模块"],
                entities=[ScoreEntityModel(type="sector", code="BK0447", name="半导体")],
            )
        ]
    )
    out = validate_node({"pending": [_FakeNews(1)], "raw": raw, "scored": {}})
    item = out["scored"][1]
    assert item.tags == ["光模块"]
    assert item.entities[0].code == "BK0447"


# ------------------------------ rescue 节点 ------------------------------


def test_rescue_rebuilds_payload_for_missing_only():
    missing = [_FakeNews(7), _FakeNews(8)]
    out = rescue_node({"missing": missing, "rounds": 0})
    assert [n.id for n in out["pending"]] == [7, 8]
    assert "1. " in out["payload"] and "2. " in out["payload"]
    assert out["rounds"] == 1
    assert out["raw"] is None  # 必须清空，否则下一轮会重复消费上一轮输出


def test_rescue_round_limit_is_honoured():
    """补打不能超过 MAX_RESCUE_ROUNDS 轮（每轮 rounds +1）。"""
    rounds = 0
    rounds_list = []
    while rounds < MAX_RESCUE_ROUNDS + 1:
        rounds_list.append(rounds)
        rounds = rescue_node({"missing": [_FakeNews(1)], "rounds": rounds})["rounds"]
    assert rounds == MAX_RESCUE_ROUNDS + 1
    assert rounds_list == list(range(MAX_RESCUE_ROUNDS + 1))


# ------------------------------ 退化护栏 ------------------------------


def _run_with_scores(scores: list[int]) -> ScoringRun:
    return ScoringRun(items={i: ScoreItemModel(id=i, score=s) for i, s in enumerate(scores, start=1)})


def test_degenerate_when_only_two_distinct_scores():
    """实测踩过：整批 20 条只给 1-2 分。"""
    assert _is_degenerate(_run_with_scores([2] * 15 + [1] * 5), 20) is True


def test_degenerate_when_coverage_too_low():
    assert _is_degenerate(_run_with_scores([1, 2, 3]), 20) is True


def test_degenerate_when_no_items():
    assert _is_degenerate(ScoringRun(), 10) is True


def test_healthy_result_is_not_degenerate():
    scores = [2, 1, 6, 6, 5, 5, 5, 5, 5, 6, 2, 5, 5, 4, 5, 2, 2, 2, 2, 1]
    assert _is_degenerate(_run_with_scores(scores), 20) is False


def test_quality_prefers_more_items_then_more_variety():
    a = _run_with_scores([5] * 10)  # 1 种分数
    b = _run_with_scores([1] + [5] * 9)  # 2 种
    c = _run_with_scores([1, 3] + [5] * 8)  # 3 种
    d = _run_with_scores([1, 3, 5])  # 3 种但只有 3 条
    assert _quality(b) > _quality(a)  # 条数相同，种类多的更优
    assert _quality(c) > _quality(b)
    assert _quality(d) < _quality(c)  # 条数优先于种类


def test_suspect_result_is_degenerate():
    run = _run_with_scores([7] * 20)
    run.is_suspect = True
    assert _is_degenerate(run, 20) is True


# ------------------------------ 分档一致性 ------------------------------


@pytest.mark.parametrize(
    "score,band",
    [(3, ScoreBand.NOISE), (4, ScoreBand.STOCK), (6, ScoreBand.INDUSTRY), (9, ScoreBand.MACRO)],
)
def test_scored_items_map_to_expected_band(score, band):
    out = validate_node({"pending": [_FakeNews(1)], "raw": _batch((1, score)), "scored": {}})
    assert band_for_score(out["scored"][1].score) is band
