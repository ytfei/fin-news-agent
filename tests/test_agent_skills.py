"""技能（skills）按名称解析、原生 Backend 与图缓存键的单元测试。

仓库没有数据库 fixture，沿用既有纯单元测试风格：用临时目录构造技能文件，
不连数据库、不调模型。

重点锁定三条不变量：
* 多搜索路径下**第一命中**生效
* 技能是**强制项**：配置了找不到必须报错中断，绝不静默跳过
* **已启用技能名**参与图缓存键（切换配置不能命中旧图）
"""
from __future__ import annotations

import pytest

from fin_news.agents.skills import (
    FilteredSkillsBackend,
    SkillNotFoundError,
    build_skills_setup,
    enabled_skill_names,
    resolve_skills,
)


def _make_skill(root, name: str, body: str = "正文内容") -> None:
    """在 root/<name>/SKILL.md 写入一个符合原生规范的技能。"""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: 测试用技能\n---\n\n{body}\n",
        encoding="utf-8",
    )


# ----------------------------------------------------------------------
# 按名称解析
# ----------------------------------------------------------------------
def test_first_search_path_wins(tmp_path):
    """同名技能存在于多个搜索路径时，排在最前的路径生效。"""
    first = tmp_path / "first"
    second = tmp_path / "second"
    _make_skill(first, "demo", body="来自 first")
    _make_skill(second, "demo", body="来自 second")

    refs = resolve_skills(["demo"], [str(first), str(second)])

    assert len(refs) == 1
    assert refs[0].root == first, "应命中第一个搜索路径"
    assert "来自 first" in refs[0].skill_md.read_text(encoding="utf-8")


def test_falls_back_to_later_path(tmp_path):
    """第一个路径没有该技能时，继续向后查找。"""
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    _make_skill(second, "demo", body="来自 second")

    refs = resolve_skills(["demo"], [str(first), str(second)])

    assert refs[0].root == second


def test_missing_skill_raises_instead_of_silently_skipping(tmp_path):
    """技能是强制项：配置了却找不到必须中断，并带上名称与已搜索路径。"""
    with pytest.raises(SkillNotFoundError) as exc:
        resolve_skills(["nope"], [str(tmp_path)], agent="macro_policy")

    msg = str(exc.value)
    assert "nope" in msg
    assert "macro_policy" in msg


def test_no_names_configured_is_valid(tmp_path):
    """未启用任何技能是合法状态，返回空列表（等价于改动前行为）。"""
    assert resolve_skills([], [str(tmp_path)]) == []
    assert build_skills_setup([], [str(tmp_path)]) is None


# ----------------------------------------------------------------------
# 配置读取
# ----------------------------------------------------------------------
def test_enabled_skill_names_are_read_per_agent():
    """按 agent_type 取值；未配置的 Agent 拿到空列表。"""
    from fin_news.core.config import Settings
    from fin_news.core.enums import AgentType

    s = Settings(_env_file=None, skills_enabled={"macro_policy": ["policy-gauge"]})

    assert enabled_skill_names(AgentType.MACRO_POLICY, s) == ["policy-gauge"]
    assert enabled_skill_names(AgentType.STOCK, s) == []


def test_skills_enabled_accepts_unquoted_json():
    """.env 被 shell source 后引号会被吃掉，解析必须容错（沿用 parse_str_list 风格）。"""
    from fin_news.core.config import Settings

    s = Settings(_env_file=None, skills_enabled="{wechat_article:[witty-tone]}")
    assert s.skills_enabled == {"wechat_article": ["witty-tone"]}


# ----------------------------------------------------------------------
# 只读 Backend
# ----------------------------------------------------------------------
def test_backend_lists_only_enabled_skills(tmp_path):
    """ls 只暴露已启用的技能，未启用的对 Agent 不可见。"""
    _make_skill(tmp_path, "enabled-one")
    _make_skill(tmp_path, "enabled-two")
    refs = resolve_skills(["enabled-one"], [str(tmp_path)])

    result = FilteredSkillsBackend(refs).ls("/")

    assert result.error is None
    assert [e["path"] for e in result.entries] == ["/enabled-one"]


def test_backend_download_reads_skill_md(tmp_path):
    """SkillsMiddleware 靠 download_files 取 SKILL.md 解析 name/description。"""
    _make_skill(tmp_path, "demo", body="技能正文")
    refs = resolve_skills(["demo"], [str(tmp_path)])

    resp = FilteredSkillsBackend(refs).download_files(["/demo/SKILL.md"])[0]

    assert resp.error is None
    assert "技能正文" in (resp.content or b"").decode("utf-8")


def test_backend_returns_file_not_found_for_unknown(tmp_path):
    """未启用 / 不存在的技能返回标准化错误，而不是抛异常中断构图。"""
    resp = FilteredSkillsBackend([]).download_files(["/other/SKILL.md"])[0]
    assert resp.error == "file_not_found"


def test_backend_blocks_path_traversal(tmp_path):
    """路径穿越必须被拒绝，防止借技能路径读到宿主机其他文件。"""
    _make_skill(tmp_path, "demo")
    refs = resolve_skills(["demo"], [str(tmp_path)])

    resp = FilteredSkillsBackend(refs).download_files(["/demo/../../etc/passwd"])[0]

    assert resp.error == "file_not_found"


def test_backend_read_pagination_fields_are_consistent(tmp_path):
    """ReadResult 对分页字段有严格校验（next_offset 必须等于 end_line）。"""
    _make_skill(tmp_path, "demo", body="\n".join(f"第{i}行" for i in range(1, 6)))
    refs = resolve_skills(["demo"], [str(tmp_path)])

    result = FilteredSkillsBackend(refs).read("/demo/SKILL.md", offset=0, limit=2)

    assert result.error is None
    assert result.start_line == 1
    assert result.end_line == 2
    assert result.next_offset == 2  # 等于 end_line，否则 ReadResult 构造即抛错
    assert result.total_lines is not None and result.total_lines >= 5


def test_backend_read_empty_file_has_no_window_fields(tmp_path):
    """空文件：窗口字段必须全部留空，否则 1 <= start_line <= end_line 不成立。"""
    d = tmp_path / "empty"
    d.mkdir()
    (d / "SKILL.md").write_text("", encoding="utf-8")  # 真空文件（不含 frontmatter）
    refs = resolve_skills(["empty"], [str(tmp_path)])

    result = FilteredSkillsBackend(refs).read("/empty/SKILL.md")

    assert result.error is None
    assert result.start_line is None and result.end_line is None


def test_backend_read_non_positive_limit_marks_no_lines(tmp_path):
    """limit<=0 表示未真正读取窗口，只能带 no_lines_requested。"""
    _make_skill(tmp_path, "demo", body="内容")
    refs = resolve_skills(["demo"], [str(tmp_path)])

    result = FilteredSkillsBackend(refs).read("/demo/SKILL.md", limit=0)

    assert result.no_lines_requested is True


# ----------------------------------------------------------------------
# 图缓存键
# ----------------------------------------------------------------------
async def test_graph_cache_key_includes_skill_names(monkeypatch, tmp_path):
    """已启用技能名参与缓存键：切换配置必须重建图，否则配置会静默失效。"""
    from fin_news.agents.graphs import analysis_graphs
    from fin_news.core.config import Settings
    from fin_news.core.enums import AgentType

    _make_skill(tmp_path, "demo")
    settings = Settings(_env_file=None)
    calls: list[tuple[str, ...]] = []

    def fake_build(agent_type, s=None, **kwargs):
        skills = kwargs.get("skills")
        calls.append(tuple(skills.names) if skills is not None else ())
        return f"graph-{len(calls)}"

    monkeypatch.setattr(analysis_graphs, "build_analysis_graph", fake_build)
    analysis_graphs.clear_cache()
    try:
        setup = build_skills_setup(["demo"], [str(tmp_path)])

        g1 = analysis_graphs.get_analysis_graph(
            AgentType.MACRO_POLICY, settings, skills=setup
        )
        g2 = analysis_graphs.get_analysis_graph(
            AgentType.MACRO_POLICY, settings, skills=setup
        )
        assert g1 == g2, "相同技能配置必须命中缓存"
        assert len(calls) == 1, f"重复构图说明缓存失效：{calls}"

        g3 = analysis_graphs.get_analysis_graph(AgentType.MACRO_POLICY, settings)
        assert g3 != g1, "技能配置变化后必须重建图"
        assert len(calls) == 2
    finally:
        analysis_graphs.clear_cache()
