"""模型单价与成本计算（**唯一**计价入口）。

历史问题（本模块存在的理由）
----------------------------
`client.py` 与 `callbacks.py` 曾各有一份重复的 `_PRICE_PER_1K_CENT`，按 role
（scoring / analysis / qa / embedding）写死单价：既不分 input / output，也不分
具体模型，且是编造的量级。结果是 `llm_call_log.cost_cent` 整列不可信，成本类
指标（单条成本、日成本、成本归因）全部无法使用。

现在统一到本模块
----------------
* 按 **model 名** 计价，区分 input / output 两档
* 单价存 config（`model_pricing`，可用环境变量覆盖），**不硬编码** —— 厂商会
  调价，硬编码必然过期（OpenTelemetry GenAI 规范对此有明确警告）
* token 已真实落库（`llm_call_log.estimated` 实测仅极少数行为 true），所以
  即使单价填错也能按新单价重算历史成本，不会污染原始数据

单位约定
--------
单价以「元 / 百万 token」配置（业界通行口径），对外计算结果为 **分**（与
`llm_call_log.cost_cent` / `agent_run.cost_cent` 的列语义保持一致）。
"""
from __future__ import annotations

from dataclasses import dataclass

from fin_news.core.config import Settings, get_settings


@dataclass(frozen=True)
class ModelPrice:
    """单个模型的单价，单位：元 / 百万 token。"""

    input_per_mtok: float
    output_per_mtok: float


# 兜底单价（元 / 百万 token）：model 未命中配置时使用。
#
# ⚠️ 这是**占位量级，不是真实报价**。请按火山方舟 / DeepSeek 控制台的实际单价，
# 通过 config 的 `model_pricing`（或环境变量 MODEL_PRICING）覆盖校准。
# 校准后可用 `fin_news.cli cost-recalc` 按新单价重算历史成本。
_FALLBACK = ModelPrice(input_per_mtok=0.8, output_per_mtok=2.0)

# 默认单价表（元 / 百万 token，区分 input / output）。
#
# ⚠️ 全部是**占位量级，不是真实报价**。务必按火山方舟 / DeepSeek 控制台的实际
# 单价，通过 config 的 `model_pricing` 覆盖校准。之所以仍填一份，是为了让
# 「未配置」时成本不为 0（成本恒为 0 会让成本类指标彻底失效，比有偏差更糟）。
#
# 模型名来自线上 llm_call_log 的实际取值 —— 注意与 `config.py` 里的默认值
# 不同：实际部署通过 .env 指向 doubao-seed 系列，seed 系列的真实单价未知，
# 这里按「模型定位」给了保守量级，待校准。
_DEFAULT_PRICING: dict[str, dict[str, float]] = {
    # 火山方舟 doubao-seed 系列（当前实际在用）
    "doubao-seed-2.0-mini": {"input": 0.3, "output": 1.2},  # 评分：轻量
    "doubao-seed-2.1-pro": {"input": 0.8, "output": 8.0},  # 分析：强推理
    "doubao-seed-evolving": {"input": 1.0, "output": 4.0},  # 分析 / 追问
    # 火山方舟 doubao 旧命名（早期数据与 config 默认值仍在用）
    "doubao-lite-32k": {"input": 0.3, "output": 0.6},
    "doubao-pro-32k": {"input": 0.8, "output": 2.0},
    "doubao-embedding-vision": {"input": 0.7, "output": 0.0},
    # DeepSeek（备 provider）
    "deepseek-chat": {"input": 2.0, "output": 8.0},
}


def _pricing_table(settings: Settings | None) -> dict[str, dict[str, float]]:
    """合并「代码默认值」与「config 覆盖值」，后者优先。"""
    table = dict(_DEFAULT_PRICING)
    cfg = (settings or get_settings()).model_pricing
    if cfg:
        table.update(cfg)
    return table


def _norm(name: str) -> str:
    """归一化模型名：转小写，并把 '.' / '_' 统一成 '-'。

    必须归一化的原因：线上同一个模型会出现两种写法 —— 基础名用点号
    （`doubao-seed-2.0-mini`），带日期后缀的快照版用连字符
    （`doubao-seed-2-0-mini-260428`）。不做归一化，后者会匹配不上而**静默**
    回落兜底价，成本数据悄悄失真且无人察觉。
    """
    return name.lower().replace(".", "-").replace("_", "-")


def _lookup(model: str, table: dict[str, dict[str, float]]) -> dict[str, float] | None:
    """三级匹配：精确 → 归一化后精确 → 归一化后前缀（取最长的基础名）。"""
    if not model:
        return None

    if model in table:
        return table[model]

    norm = _norm(model)
    for key, value in table.items():
        if _norm(key) == norm:
            return value

    candidates = [k for k in table if norm.startswith(_norm(k))]
    if candidates:
        return table[max(candidates, key=lambda k: len(_norm(k)))]
    return None


def price_of(model: str, settings: Settings | None = None) -> ModelPrice:
    """按 model 名查单价；未命中回落到兜底档。

    是否命中可用 `is_priced()` 判断 —— 未命中意味着该模型的成本是估算值，
    应当补进 `model_pricing` 配置（监控面板会对此给出提示）。
    """
    raw = _lookup(model, _pricing_table(settings))
    if raw is None:
        return _FALLBACK
    try:
        return ModelPrice(
            input_per_mtok=float(raw.get("input", _FALLBACK.input_per_mtok)),
            output_per_mtok=float(raw.get("output", _FALLBACK.output_per_mtok)),
        )
    except (TypeError, ValueError):
        return _FALLBACK


def is_priced(model: str, settings: Settings | None = None) -> bool:
    """该模型是否命中单价配置。

    false 表示成本是兜底估算 —— 这类模型会被监控面板单独列出，提醒补配置，
    避免「成本看着有数，其实全是估的」。
    """
    return _lookup(model, _pricing_table(settings)) is not None


def calc_cost_cent(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    *,
    settings: Settings | None = None,
) -> float:
    """计算单次调用成本（分）。

    input / output 分开计价；token 为 0 或缺失时成本为 0（不做估算，
    避免和 `estimated` 标记的兜底 token 混在一起导致成本虚高）。
    """
    p = price_of(model, settings)
    cost_yuan = (
        (prompt_tokens or 0) / 1_000_000 * p.input_per_mtok
        + (completion_tokens or 0) / 1_000_000 * p.output_per_mtok
    )
    return round(cost_yuan * 100, 4)
