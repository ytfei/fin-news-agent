"""Skills 包：微信公众号 Agent 的技能加载机制。

支持两种技能：
- 提示词型：`skills/<name>/SKILL.md`（YAML frontmatter + markdown 正文），加载后注入 system prompt。
- 工具型：`skills/<name>/tool.py`（导出 `get_tool()` 返回 LangChain 工具），挂入 Agent 的 tools。
"""
from fin_news.agents.skills.loader import (
    PromptSkill,
    SkillsBundle,
    load_skills,
    render_prompt_suffix,
)

__all__ = [
    "PromptSkill",
    "SkillsBundle",
    "load_skills",
    "render_prompt_suffix",
]
