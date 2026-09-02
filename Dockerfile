# fin-news-v5 后端镜像：API + APScheduler 调度器 + Pipeline worker 三合一
# 多阶段构建：base 层装依赖（层缓存复用）→ runtime 层只拷贝产物（镜像更小）
# 构建：docker build -t fin-news-app .

# ============ base：依赖构建层 ============
FROM python:3.13-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # copy 模式让 .venv 自包含（非 hardlink），可安全跨阶段 COPY --from
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

# 安装 uv（官方镜像）
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# 先装第三方依赖（不含项目）：pyproject/uv.lock 不变则整层缓存命中，无需重下依赖
# README.md 必须带上：pyproject.toml 里 readme = "README.md"，hatchling 构建会校验
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# 再装项目本体（非 editable，打成 wheel 进 .venv/site-packages）
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

# 迁移脚本与配置（运行 alembic upgrade head 需要，独立于 wheel）
COPY alembic ./alembic
COPY alembic.ini ./

# ============ runtime：运行时层 ============
FROM python:3.13-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai

# 时区：盘前/盘后调度与接入时间窗依赖本地时区（slim 镜像默认无 tzdata）
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 从 base 层拷贝已装好的 .venv（第三方依赖 + 项目本体），不含 uv 工具与构建缓存
COPY --from=base /app/.venv /app/.venv

# 迁移脚本与配置
COPY --from=base /app/alembic ./alembic
COPY --from=base /app/alembic.ini ./

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# 启动前先跑迁移（幂等，alembic 会跳过已应用的版本），再启动三合一服务
CMD ["sh", "-c", "alembic upgrade head && python -m fin_news.main"]
