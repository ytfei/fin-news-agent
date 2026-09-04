"""Skills 加载器：扫描 skills 目录，加载提示词型 + 工具型技能。

目录约定（每个技能一个子目录）：
    skills/
    └── <skill-name>/
        ├── SKILL.md     # 提示词型：YAML frontmatter + markdown 正文
        └── tool.py      # 工具型：导出 get_tool() -> LangChain 工具

SKILL.md frontmatter 字段：
    name         技能名（缺省用目录名）
    description  一句话说明
    when_to_use  何时启用该技能

加载是幂等且容错的：目录不存在返回空 bundle；单个 skill 解析失败只告警、不影响其余。
"""
from __future__ import annotations

import importlib.util
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from fin_news.core.config import Settings, get_settings
from fin_news.core.logging import get_logger

logger = get_logger("agents.skills.loader")


@dataclass
class PromptSkill:
    """提示词型技能：加载后注入 system prompt。"""

    name: str
    description: str = ""
    when_to_use: str = ""
    body: str = ""


@dataclass
class SkillsBundle:
    """一次加载的结果：提示词型技能列表 + 工具型技能（LangChain 工具）列表。"""

    prompt_skills: list[PromptSkill] = field(default_factory=list)
    tool_skills: list[Any] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.prompt_skills and not self.tool_skills


_FRONTMATTER_RE = re.compile(r"^\s*---\s*\n(.*?)\n\s*---\s*\n?(.*)$", re.DOTALL)


def load_skills(skills_dir: str | Path | None = None, settings: Settings | None = None) -> SkillsBundle:
    """扫描 skills_dir，加载全部技能。目录不存在或为 None 时返回空 bundle。"""
    settings = settings or get_settings()
    path = Path(skills_dir or settings.skills_dir)
    if not path.is_dir():
        if skills_dir is not None:
            logger.warning("skills 目录不存在", skills_dir=str(path))
        return SkillsBundle()

    bundle = SkillsBundle()
    for entry in sorted(path.iterdir()):
        if not entry.is_dir():
            continue
        prompt_skill = _load_prompt_skill(entry)
        if prompt_skill is not None:
            bundle.prompt_skills.append(prompt_skill)
        bundle.tool_skills.extend(_load_tool_skills(entry))
    logger.info(
        "skills 加载完成",
        skills_dir=str(path),
        prompt_skills=len(bundle.prompt_skills),
        tool_skills=len(bundle.tool_skills),
    )
    return bundle


def _load_prompt_skill(skill_dir: Path) -> PromptSkill | None:
    md_path = skill_dir / "SKILL.md"
    if not md_path.is_file():
        return None
    try:
        text = md_path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("读取 SKILL.md 失败", skill=skill_dir.name, error=str(exc)[:200])
        return None

    meta: dict[str, Any] = {}
    body = text.strip()
    m = _FRONTMATTER_RE.match(text)
    if m:
        try:
            parsed = yaml.safe_load(m.group(1))
            if isinstance(parsed, dict):
                meta = parsed
        except yaml.YAMLError as exc:
            logger.warning("SKILL.md frontmatter 解析失败，按纯正文处理", skill=skill_dir.name, error=str(exc)[:200])
        body = m.group(2).strip()

    return PromptSkill(
        name=str(meta.get("name") or skill_dir.name),
        description=str(meta.get("description") or ""),
        when_to_use=str(meta.get("when_to_use") or ""),
        body=body,
    )


def _load_tool_skills(skill_dir: Path) -> list[Any]:
    """加载 tool.py，返回它导出的工具列表（get_tool() 优先）。"""
    tool_path = skill_dir / "tool.py"
    if not tool_path.is_file():
        return []
    try:
        spec = importlib.util.spec_from_file_location(
            f"fin_news_skill_{skill_dir.name}", str(tool_path)
        )
        if spec is None or spec.loader is None:
            return []
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - 单个 skill 失败不影响其余
        logger.warning("导入 tool.py 失败", skill=skill_dir.name, error=str(exc)[:200])
        return []

    # 优先 get_tool()；兼容 TOOLS / TOOL 属性
    get_tool = getattr(module, "get_tool", None)
    if callable(get_tool):
        try:
            result = get_tool()
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_tool() 执行失败", skill=skill_dir.name, error=str(exc)[:200])
            return []
        return result if isinstance(result, list) else [result]

    for attr in ("TOOLS", "TOOL"):
        val = getattr(module, attr, None)
        if val is not None:
            return val if isinstance(val, list) else [val]
    logger.warning("tool.py 未导出 get_tool()/TOOLS/TOOL，跳过", skill=skill_dir.name)
    return []


def render_prompt_suffix(bundle: SkillsBundle) -> str:
    """把提示词型技能渲染成 system prompt 的追加段落。"""
    if not bundle.prompt_skills:
        return ""
    blocks = ["", "## 可用技能（Skills）", ""]
    for skill in bundle.prompt_skills:
        header = f"### 技能：{skill.name}"
        if skill.description:
            header += f" —— {skill.description}"
        blocks.append(header)
        if skill.when_to_use:
            blocks.append(f"适用时机：{skill.when_to_use}")
        if skill.body:
            blocks.append("")
            blocks.append(skill.body)
        blocks.append("")
    return "\n".join(blocks).rstrip() + "\n"
