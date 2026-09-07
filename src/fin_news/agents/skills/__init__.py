"""Skills 包：技能加载机制。

两类技能走不同通道：

- **提示词型** `skills/<name>/SKILL.md`（YAML frontmatter + markdown 正文）
  走 **DeepAgents 原生** SkillsMiddleware：只把 name/description/path 放进
  system prompt，正文由 Agent 用 `read_file` 按需读取（渐进式披露，省 token）。
  原生只认「目录 source」，不支持按名称挑选，因此由 `registry.resolve_skills`
  先按名称解析，再用 `FilteredSkillsBackend` 只暴露已启用的技能。
- **工具型** `skills/<name>/tool.py`（导出 `get_tool()` 返回 LangChain 工具）
  原生**不支持**通过技能定义新工具（只有 `allowed-tools` 引用已有工具），
  因此继续走自研 `loader.load_skills`，产出的工具通过 `extra_tools` 挂入 Agent。
"""
from fin_news.agents.skills.loader import (
    PromptSkill,
    SkillsBundle,
    load_skills,
    render_prompt_suffix,
)
from fin_news.agents.skills.registry import (
    FilteredSkillsBackend,
    SkillNotFoundError,
    SkillRef,
    SkillsSetup,
    build_skills_setup,
    enabled_skill_names,
    resolve_skills,
    with_skills_fallback,
)

__all__ = [
    "FilteredSkillsBackend",
    "PromptSkill",
    "SkillNotFoundError",
    "SkillRef",
    "SkillsBundle",
    "SkillsSetup",
    "build_skills_setup",
    "enabled_skill_names",
    "load_skills",
    "render_prompt_suffix",
    "resolve_skills",
    "with_skills_fallback",
]
