# Skills 目录

所有技能**平铺**放在本目录下（每个技能一个子目录）。至于**哪些 Agent 启用哪些技能**，
由配置决定，不由目录结构决定。

## 目录结构

```
skills/
└── <skill-name>/
    ├── SKILL.md    # 提示词型（可选）
    └── tool.py     # 工具型（可选）
```

一个技能可以只有 `SKILL.md`、只有 `tool.py`，或两者都有。

## 启用方式（配置驱动）

在 `.env` 里配置两件事：

```bash
# 1) 搜索路径：按序查找，给定技能名命中第一个存在的即停
SKILLS_SEARCH_PATHS=["skills","~/.fin-news/skills"]

# 2) 各 Agent 启用的技能名：key 用 agent_type 的值
SKILLS_ENABLED={"macro_policy":["policy-gauge"],"wechat_article":["witty-tone"]}
```

可选的 agent key：`macro_policy`、`industry`、`stock`、`pre_market`、`post_market`、
`wechat_article`。未配置的 Agent 不启用任何技能。

写文章 Agent 的 CLI 参数 `--skills-dir` 会**覆盖**搜索路径（优先级高于配置）。

> ⚠️ **技能是强制项**：配置了某个技能名，但在所有搜索路径里都找不到时，
> Agent 会**直接报错中断**，不会静默跳过。这是刻意的 —— 静默降级会让
> 「配了却没生效」变成最难排查的问题。

## 两种形态

### 1. 提示词型（`SKILL.md`）

走 **DeepAgents 原生**机制（`create_deep_agent(skills=...)` + `SkillsMiddleware`），
采用**渐进式披露**：

- 系统只把技能的 `name` + `description` + 路径放进 system prompt
- Agent 判断任务与技能相关时，才用 `read_file` 读取正文
- 好处：技能再多也不会把所有正文塞进 prompt，token 成本只与实际用到的技能相关

格式（YAML frontmatter + markdown 正文）：

```markdown
---
name: policy-gauge
description: 评估宏观政策的实际力度与执行确定性
---

正文：具体的工作流、清单、注意事项……
```

- `name`：技能名（1-64 字符，**必填**）
- `description`：一句话说明（**必填**，Agent 靠它判断是否相关，写清楚很关键）
- `allowed-tools`：可选，限制该技能可用的工具名

### 2. 工具型（`tool.py`）

原生**不支持**通过技能定义新工具（原生只有 `allowed-tools` 引用已有工具），
因此工具型走自研加载：导出 `get_tool()` 返回一个 LangChain 工具（或工具列表），
加载后挂进 Agent 的 `tools`。

```python
from langchain_core.tools import tool

@tool
def generate_cover(title: str, summary: str) -> str:
    """根据标题与摘要生成封面图，返回图片 URL。"""
    ...

def get_tool():
    return generate_cover
```

也兼容导出 `TOOLS` / `TOOL` 属性。工具内需要查库时，用
`async with session_scope() as session:` 自行开会话（与内置工具一致）。
单个技能加载失败只告警，不影响其余技能。

## 作用域与安全

- 技能经只读 Backend 暴露在 Agent 虚拟文件系统的 `/skills/` 下，其余路径不受影响
- 路径穿越（`..`）会被拒绝，技能正文只读

## 缓存说明

- **已启用技能名**参与图缓存键：切换配置会重建图，不会命中旧图
- 技能**正文**修改无需清缓存：原生在每次运行时从后端现读
