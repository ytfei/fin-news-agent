"""微信公众号文章 Agent 的纯逻辑单测：模型 / skills 加载器 / 检索 SQL。"""
from __future__ import annotations

from datetime import date

from sqlalchemy.dialects import postgresql

# ------------------------------ 枚举与模型 ------------------------------


def test_article_status_enum_values():
    from fin_news.core.enums import ArticleStatus

    assert [s.value for s in ArticleStatus] == ["NEW", "DRAFT", "PUBLISHED", "DELETED"]


def test_article_payload_defaults():
    from fin_news.agents.schemas import ArticlePayload

    p = ArticlePayload()
    assert p.title == ""
    assert p.summary == ""
    assert p.content == ""
    assert p.tags == []
    assert p.referenced_article_ids == []
    assert p.cover_hint is None


def test_wechat_article_model_fields():
    from fin_news.models.wechat import WechatArticle

    a = WechatArticle(title="标题", content="正文", publish_date=date(2026, 9, 4))
    assert a.title == "标题"
    assert a.content == "正文"
    assert a.publish_date == date(2026, 9, 4)
    assert a.cover_image is None
    assert a.cover_hint is None


# ------------------------------ skills 加载器 ------------------------------


def test_skills_loader_prompt_skill(tmp_path):
    from fin_news.agents.skills import load_skills

    skill_dir = tmp_path / "banter"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: 插科打诨\ndescription: 口语化\nwhen_to_use: 写正文时\n---\n正文：多用短句。\n",
        encoding="utf-8",
    )

    bundle = load_skills(str(tmp_path))
    assert len(bundle.prompt_skills) == 1
    s = bundle.prompt_skills[0]
    assert s.name == "插科打诨"
    assert s.description == "口语化"
    assert s.when_to_use == "写正文时"
    assert s.body == "正文：多用短句。"


def test_skills_loader_tool_skill(tmp_path):
    from fin_news.agents.skills import load_skills

    skill_dir = tmp_path / "gen-cover"
    skill_dir.mkdir()
    (skill_dir / "tool.py").write_text(
        "from langchain_core.tools import tool\n"
        "@tool\n"
        "def generate_cover(title: str) -> str:\n"
        "    '''生成封面。'''\n"
        "    return title\n"
        "def get_tool():\n"
        "    return generate_cover\n",
        encoding="utf-8",
    )

    bundle = load_skills(str(tmp_path))
    assert len(bundle.tool_skills) == 1
    assert bundle.tool_skills[0].name == "generate_cover"


def test_skills_loader_missing_dir_returns_empty():
    from fin_news.agents.skills import load_skills

    assert load_skills("/nonexistent/skills-dir").is_empty


def test_render_prompt_suffix():
    from fin_news.agents.skills import PromptSkill, SkillsBundle, render_prompt_suffix

    bundle = SkillsBundle(
        prompt_skills=[PromptSkill(name="s", description="d", when_to_use="w", body="技能正文")]
    )
    out = render_prompt_suffix(bundle)
    assert "技能：s" in out
    assert "适用时机：w" in out
    assert "技能正文" in out
    assert render_prompt_suffix(SkillsBundle()) == ""


# ------------------------------ 历史文章检索 SQL ------------------------------


def test_article_search_stmt_filters_published_only():
    from fin_news.agents.tools.article_retrieval import _article_search_stmt
    from fin_news.core.enums import ArticleStatus

    compiled = _article_search_stmt([0.1, 0.2], top_k=8).compile(dialect=postgresql.dialect())
    sql = str(compiled)
    # 核心安全要求：只能检索已发布文章，SQL 层强制 status == PUBLISHED
    assert "wechat_article.status" in sql
    assert "wechat_article_chunk" in sql
    assert compiled.params["status_1"] == ArticleStatus.PUBLISHED


def test_format_article_hits():
    from fin_news.agents.tools.article_retrieval import ArticleHit, format_article_hits

    hits = [
        ArticleHit(
            article_id=1,
            public_id="abc",
            chunk_id=1,
            title="昨天的文章",
            snippet="讲降准",
            publish_date=date(2026, 9, 3),
            similarity=0.9,
        )
    ]
    out = format_article_hits(hits)
    assert "《昨天的文章》" in out
    assert "2026-09-03" in out
    assert "讲降准" in out
    assert format_article_hits([]) == "（未检索到相关历史文章）"
