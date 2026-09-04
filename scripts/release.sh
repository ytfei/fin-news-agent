#!/usr/bin/env bash
# 一条命令发布：更新 compose 版本 → 提交 → 打 tag → 推送。
#
# 用法：
#   ./scripts/release.sh v1.2.3
#   git release v1.2.3          # 配置别名后（见文件底部说明）
#
# 流程与 docker-release.yml 配合：
#   1) 把 docker-compose.prod.yml 的镜像版本改为 <version>
#   2) 提交该变更
#   3) 在当前 commit 上打 tag <version>
#   4) 推送分支 + tag；推送后由 CI（docker-release.yml）构建并推送镜像
set -euo pipefail

VERSION="${1:-}"
if [[ -z "$VERSION" ]]; then
  echo "用法: $0 <version>   例如: $0 v1.2.3" >&2
  exit 1
fi

# 与 workflow 的 tags: ["v*"] 保持一致，同时避免意外打进非法版本号
if [[ ! "$VERSION" =~ ^v[0-9] ]]; then
  echo "错误：版本号需以 v 开头且为数字，例如 v1.2.3" >&2
  exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# 已在远程或本地存在同名 tag 则直接报错，防止覆盖
if git rev-parse -q --verify "refs/tags/$VERSION" >/dev/null 2>&1; then
  echo "错误：tag $VERSION 已存在" >&2
  exit 1
fi

BRANCH="$(git symbolic-ref --short -q HEAD || true)"
if [[ "$BRANCH" != "main" && "$BRANCH" != "master" ]]; then
  echo "警告：当前分支为 '$BRANCH'（非 main/master），发布通常应在 main 上执行。" >&2
fi

# 工作区 / 暂存区必须干净，避免把无关改动混进 release
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "错误：工作区有未提交改动，请先提交或暂存后再发布。" >&2
  exit 1
fi

# 1) 更新 compose 镜像版本
"$ROOT/scripts/bump-compose.sh" "$VERSION"

# 2) 提交版本变更（无变化则跳过）
if git diff --quiet -- docker-compose.prod.yml; then
  echo "docker-compose.prod.yml 版本无变化，跳过提交。"
else
  git add docker-compose.prod.yml
  git commit -m "chore(release): 生产镜像引用更新至 $VERSION"
fi

# 3) 打 tag
git tag "$VERSION"

# 4) 推送分支与 tag（pre-push hook 会在此前校验；compose 已同步，故为 no-op）
echo ""
echo "==> 推送 $BRANCH 与 tag $VERSION"
git push origin "$BRANCH" "$VERSION"

echo ""
echo "✅ 已发布 $VERSION"
echo "   - docker-release.yml 将自动构建并推送镜像到 ghcr.io"
echo "   - 生产环境部署：docker compose -f docker-compose.prod.yml pull && docker compose -f docker-compose.prod.yml up -d"
