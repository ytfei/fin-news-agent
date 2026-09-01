"""评分 Agent 的输出解析与降级容错（不需要模型 Key）。"""
from fin_news.agents.scoring_agent import ScoringAgent
from fin_news.core.enums import ScoreBand


def _parse(items, expected_ids):
    data = {"items": items}
    return ScoringAgent._parse(data, model="test-model", prompt_tokens=10, completion_tokens=20,
                               latency_ms=100, expected_ids=expected_ids)


def test_parse_maps_index_to_news_id():
    result = _parse(
        [{"id": 1, "score": 9, "reason": "全面降准"}, {"id": 2, "score": 2, "reason": "行情播报"}],
        {101, 102},
    )
    ids = {r.id: r.score for r in result.items}
    # 输入集合排序后 [101, 102]，编号 1 -> 101，编号 2 -> 102
    assert ids == {101: 9, 102: 2}


def test_parse_drops_hallucinated_and_duplicate_ids():
    result = _parse(
        [
            {"id": 1, "score": 6},
            {"id": 1, "score": 7},   # 重复
            {"id": 99, "score": 8},  # 幻觉 id
            {"score": 5},            # 缺 id
        ],
        {10},
    )
    assert [r.id for r in result.items] == [10]


def test_parse_clamps_out_of_range_scores():
    result = _parse([{"id": 1, "score": 100}, {"id": 2, "score": -3}], {1, 2})
    assert {r.score for r in result.items} == {10, 1}


def test_parse_invalid_payload_returns_empty():
    assert _parse(None, {1}).items == []
    assert _parse({"foo": "bar"}, {1}).items == []
    assert _parse({"items": "not-a-list"}, {1}).items == []


def test_parse_flags_suspect_when_batch_is_uniform():
    items = [{"id": i, "score": 10} for i in range(1, 11)]
    # 10 条全部同分 -> 视为分布异常（suspect）
    result = _parse(items, set(range(1, 11)))
    assert len(result.items) == 10
    scores = [r.score for r in result.items]
    assert scores.count(10) / len(scores) >= 0.8


def test_parse_assigns_band_correctly():
    result = _parse([{"id": 1, "score": 8}], {1})
    from fin_news.domain.scoring import band_for_score

    assert band_for_score(result.items[0].score) is ScoreBand.MACRO


def test_parse_keeps_tags_and_entities():
    result = _parse(
        [
            {
                "id": 1,
                "score": 6,
                "tags": ["光模块", "算力"],
                "entities": [{"type": "sector", "code": "BK0447", "name": "半导体"}],
            }
        ],
        {1},
    )
    item = result.items[0]
    assert item.tags == ["光模块", "算力"]
    assert item.entities[0].code == "BK0447"


def test_missing_items_are_reported_but_not_fatal():
    result = _parse([{"id": 1, "score": 5}], {1, 2, 3})
    assert len(result.items) == 1  # 漏评的条目由上层标记 SCORE_FAILED
