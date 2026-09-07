"""按名称解析技能 + 供 DeepAgents 原生 SkillsMiddleware 使用的只读 Backend。

设计背景
--------
DeepAgents 原生的 `create_deep_agent(skills=[...])` 接受的是**目录 source 路径**，
扫描其下所有含 SKILL.md 的子目录，**没有「只启用其中某几个」的参数**。而本项目
要求「所有技能平铺在同一目录，由 settings 指定每个 Agent 启用哪些名称」，并且
搜索路径可配置多组、按序第一命中。

解决办法是把「按名称挑选」下沉到 Backend 层：自定义一个只暴露**已启用技能**的
只读 Backend，再经 `CompositeBackend` 挂到 `/skills/` 前缀下。这样原生中间件照常
工作（渐进式披露：只把 name+description+path 放进 system prompt，正文由 Agent 用
`read_file` 按需读取），同时作用域被限制在允许的少数技能上，其余路径仍回落
StateBackend，不影响 Agent 的其他文件操作。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from deepagents.backends import CompositeBackend, StateBackend
from deepagents.backends.protocol import (
    BackendProtocol,
    FileDownloadResponse,
    FileInfo,
    LsResult,
    ReadResult,
)

from fin_news.core.logging import get_logger

logger = get_logger("agents.skills.registry")

SKILL_FILENAME = "SKILL.md"

# 技能在 Agent 虚拟文件系统中的挂载前缀（原生 source 路径即此值）
SKILLS_ROUTE = "/skills/"


class SkillNotFoundError(RuntimeError):
    """配置启用的技能在全部搜索路径中都找不到。

    技能是**强制项**：宁可中断也不能静默跳过。静默降级会让「配了却没生效」
    变成最难排查的问题（表面正常运行，实际分析质量悄悄下降）。
    """


@dataclass(frozen=True)
class SkillRef:
    """一个已解析到的技能（含它实际所在的搜索路径）。"""

    name: str
    skill_dir: Path  # 技能目录（内含 SKILL.md）
    root: Path  # 命中的搜索路径（便于日志定位）

    @property
    def skill_md(self) -> Path:
        return self.skill_dir / SKILL_FILENAME


def resolve_skills(
    names: list[str],
    search_paths: list[str],
    *,
    agent: str | None = None,
) -> list[SkillRef]:
    """按名称在搜索路径中查找技能，返回**第一命中**的结果。

    搜索语义：对每个名称，按 `search_paths` 顺序逐个尝试 `<path>/<name>/SKILL.md`，
    命中第一个存在的文件即停止（后续路径中的同名技能不生效）。

    缺失即抛 `SkillNotFoundError`（携带全部已搜索路径便于定位）。`names` 为空时
    返回空列表 —— 未配置技能是合法的，等同于现状行为。
    """
    if not names:
        return []

    roots = [Path(p).expanduser() for p in search_paths]
    refs: list[SkillRef] = []

    for name in names:
        for root in roots:
            candidate = root / name
            if (candidate / SKILL_FILENAME).is_file():
                refs.append(SkillRef(name=name, skill_dir=candidate, root=root))
                break
        else:
            raise SkillNotFoundError(
                f"Agent '{agent or '-'}' 启用了技能 '{name}'，"
                f"但在搜索路径中均未找到 {SKILL_FILENAME}：{[str(r) for r in roots]}"
            )

    logger.info(
        "技能解析完成",
        agent=agent,
        requested=list(names),
        resolved=[r.name for r in refs],
        roots=[str(r) for r in roots],
    )
    return refs


class FilteredSkillsBackend(BackendProtocol):
    """只暴露「已启用技能」的只读 Backend。

    虚拟路径布局（由 CompositeBackend 挂载到 `/skills/` 前缀下之后）：
        /                    技能根目录
        /<name>/             技能目录
        /<name>/SKILL.md     技能定义

    只需实现三个方法：`ls`（SkillsMiddleware 发现技能）、`download_files`
    （批量取 SKILL.md 解析元数据）、`read`（Agent 用 read_file 读正文）。
    BackendProtocol 明确允许子类只实现所需子集（见其类注释）。

    安全：所有访问都被限制在已解析技能目录内，路径穿越（`..`）一律拒绝。
    """

    def __init__(self, refs: list[SkillRef]) -> None:
        self._refs = {r.name: r for r in refs}

    # ------------------------------------------------------------------
    # 路径映射
    # ------------------------------------------------------------------
    def _resolve(self, path: str) -> Path | None:
        """把虚拟路径映射到宿主机真实路径；越界或不存在返回 None。"""
        parts = [p for p in path.strip("/").split("/") if p]
        if not parts:
            return None
        ref = self._refs.get(parts[0])
        if ref is None:
            return None

        # 只允许访问该技能目录内的文件（未给文件名时默认为 SKILL.md）
        rel = Path(*parts[1:]) if len(parts) > 1 else Path(SKILL_FILENAME)
        root = ref.skill_dir.resolve()
        target = (ref.skill_dir / rel).resolve()
        if target != root and root not in target.parents:
            return None
        return target

    # ------------------------------------------------------------------
    # BackendProtocol 子集
    # ------------------------------------------------------------------
    def ls(self, path: str) -> LsResult:
        """列出技能；传入具体技能目录时列出其下的文件。"""
        parts = [p for p in path.strip("/").split("/") if p]

        if not parts:
            entries: list[FileInfo] = [
                {"path": f"/{name}", "is_dir": True} for name in self._refs
            ]
            return LsResult(entries=entries)

        ref = self._refs.get(parts[0])
        if ref is None:
            return LsResult(error="file_not_found")

        base = ref.skill_dir / Path(*parts[1:])
        if not base.is_dir():
            return LsResult(error="file_not_found")
        try:
            entries = [
                {"path": f"/{parts[0]}/{child.name}", "is_dir": child.is_dir()}
                for child in sorted(base.iterdir())
            ]
        except OSError as exc:
            return LsResult(error=str(exc))
        return LsResult(entries=entries)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """批量下载（SkillsMiddleware 用它取 SKILL.md 解析 name/description）。"""
        responses: list[FileDownloadResponse] = []
        for p in paths:
            target = self._resolve(p)
            if target is None or not target.is_file():
                responses.append(
                    FileDownloadResponse(path=p, content=None, error="file_not_found")
                )
                continue
            try:
                responses.append(
                    FileDownloadResponse(path=p, content=target.read_bytes(), error=None)
                )
            except OSError as exc:
                responses.append(FileDownloadResponse(path=p, content=None, error=str(exc)))
        return responses

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        """read_file 读取技能正文（渐进式披露：Agent 判定相关后才读）。

        ReadResult 对分页字段有严格校验（start_line/end_line 必须成对、next_offset
        必须等于 end_line、total_lines 不得小于 end_line），因此空文件与非正 limit
        必须走各自的分支，否则构造即抛 ValueError。
        """
        if limit <= 0:
            # 未真正读取窗口：只能带 no_lines_requested，不能带任何分页字段
            return ReadResult(no_lines_requested=True)

        target = self._resolve(file_path)
        if target is None or not target.is_file():
            return ReadResult(error="file_not_found")
        try:
            text = target.read_text(encoding="utf-8")
        except OSError as exc:
            return ReadResult(error=str(exc))

        lines = text.splitlines()
        if not lines:
            # 空文件：窗口字段必须全部留空，否则 1 <= start_line <= end_line 不成立
            return ReadResult(file_data={"content": "", "encoding": "utf-8"})

        start = max(0, offset)
        end = min(len(lines), start + limit)
        # 已读到末尾时 next_offset 必须留空（它要求等于 end_line，留空表示无下一页）
        next_offset = end if end < len(lines) else None
        return ReadResult(
            file_data={"content": "\n".join(lines[start:end]), "encoding": "utf-8"},
            total_lines=len(lines),
            start_line=start + 1,
            end_line=end,
            next_offset=next_offset,
        )


def with_skills_fallback(system_prompt: str, agent_type: Any, settings: Any) -> str:
    """降级路径的技能兜底：把已启用技能的正文**全量**拼进 system prompt。

    正常路径由原生 SkillsMiddleware 做渐进式披露（省 token）。但 DeepAgents 图
    失败会降级为单次结构化调用，此时中间件不生效、技能会静默失效 —— 而技能是
    **强制项**，所以降级路径改为直接注入正文兜底。

    仅注入该 Agent 已启用的技能（按名称解析），未配置的不会混入。
    """
    names = enabled_skill_names(agent_type, settings)
    if not names:
        return system_prompt

    refs = resolve_skills(
        names, settings.skills_search_paths, agent=getattr(agent_type, "value", str(agent_type))
    )
    blocks = [system_prompt, "", "## 可用技能（Skills）", ""]
    for ref in refs:
        try:
            body = ref.skill_md.read_text(encoding="utf-8").strip()
        except OSError as exc:
            # 解析阶段已确认文件存在，这里读不到属于运行期异常，必须暴露
            raise SkillNotFoundError(f"技能 '{ref.name}' 正文读取失败：{exc}") from exc
        blocks.append(f"### 技能：{ref.name}")
        blocks.append(body)
        blocks.append("")
    return "\n".join(blocks).rstrip() + "\n"


@dataclass(frozen=True)
class SkillsSetup:
    """一次技能装配的结果，供构图函数使用。

    - `names`：已启用技能名 —— 参与**图缓存键**，必须可哈希
    - `sources`：传给原生 `create_deep_agent(skills=...)` 的来源路径
    - `backend`：装配好的 Backend 实例（对象，不可哈希，**不进缓存键**）
    - `refs`：已解析的技能（供工具型技能按目录加载 `tool.py`，避免重复解析）
    """

    names: tuple[str, ...]
    sources: tuple[str, ...]
    backend: Any
    refs: tuple[SkillRef, ...] = ()


def enabled_skill_names(agent_type: Any, settings: Any) -> list[str]:
    """取 settings 中为该 Agent 配置的启用技能名；未配置返回空列表。

    未配置是合法的「不启用技能」，与配置后找不到（报错中断）是两回事。
    """
    agent_key = getattr(agent_type, "value", str(agent_type))
    return list((getattr(settings, "skills_enabled", None) or {}).get(agent_key, []))


def build_skills_setup(
    names: list[str],
    search_paths: list[str],
    *,
    agent: str | None = None,
) -> SkillsSetup | None:
    """按名称解析技能并装配原生技能后端；未启用任何技能时返回 None。

    返回 None 表示「该 Agent 不启用技能」，调用方应保持现状行为（不传
    skills / backend），确保未配置时与改动前完全一致。
    """
    if not names:
        return None

    refs = resolve_skills(names, search_paths, agent=agent)
    # 只把 /skills/ 路由到只读技能后端；其余路径回落 StateBackend，
    # 避免整体替换 backend 后波及 Agent 的其他文件操作。
    backend = CompositeBackend(
        default=StateBackend(),
        routes={SKILLS_ROUTE: FilteredSkillsBackend(refs)},
    )
    return SkillsSetup(
        names=tuple(r.name for r in refs),
        sources=(SKILLS_ROUTE,),
        backend=backend,
        refs=tuple(refs),
    )
