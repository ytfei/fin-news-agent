# Skills 目录

微信公众号文章 Agent 的技能目录。每个技能一个子目录，支持两种形态：

## 1. 提示词型技能（`SKILL.md`）

写文章时把技能说明注入 Agent 的 system prompt，影响写法、风格或约束。

目录结构：

```
skills/
└── <skill-name>/
    └── SKILL.md
```

`SKILL.md` 格式（YAML frontmatter + markdown 正文）：

```markdown
---
name: 插科打诨
description: 让文章语气更口语化、有网感
when_to_use: 撰写公众号正文时始终启用
---
写作时注意：
1. 用「咱就是说」「懂的都懂」这类口语，但不要滥用。
2. 分析要专业，俏皮话只用来调节节奏，不能替代结论。
3. 每个段落控制在 2-3 句，别写长难句。
```

- `name`：技能名（缺省用目录名）
- `description`：一句话说明
- `when_to_use`：何时启用

## 2. 工具型技能（`tool.py`）

暴露成一个 Agent 可调用的 LangChain 工具，例如「生成封面」「发布文章」。

目录结构：

```
skills/
└── <skill-name>/
    └── tool.py
```

`tool.py` 约定：导出 `get_tool()`，返回一个 LangChain 工具（或工具列表）。也兼容导出 `TOOLS` / `TOOL` 属性。

```python
from langchain_core.tools import tool

@tool
def generate_cover(title: str, summary: str) -> str:
    """根据标题与摘要生成公众号封面图，返回图片 URL。"""
    ...

def get_tool():
    return generate_cover
```

## 目录可配置

- 默认目录：`skills`（由 `settings.skills_dir` 决定）
- 覆盖：`python -m fin_news.cli article write --skills-dir /path/to/skills`

## 未来预留

「生成封面图」「发布到公众号」都将以**工具型技能**的形式放进本目录（各自维护自己的凭证/配置），
写文章 Agent 通过 skills 加载器自动挂载并调用，无需改动主流程代码。
