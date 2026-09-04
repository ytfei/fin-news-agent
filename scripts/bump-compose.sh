#!/usr/bin/env bash
# 把 docker-compose.prod.yml 中 app/web 两个镜像的版本号统一改为指定版本。
# 用法：scripts/bump-compose.sh v1.2.3
set -euo pipefail

VERSION="${1:-}"
if [[ -z "$VERSION" ]]; then
  echo "用法: $0 <version>   例如: $0 v1.2.3" >&2
  exit 1
fi

# 脚本可能在仓库任意子目录被调用，统一定位到仓库根目录
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT/docker-compose.prod.yml"

if [[ ! -f "$COMPOSE_FILE" ]]; then
  echo "未找到 $COMPOSE_FILE" >&2
  exit 1
fi

# 用临时文件 + mv 改写，避免 GNU/BSD(macOS) 的 `sed -i` 参数差异
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

sed \
  "s#\(ghcr\.io/[^/[:space:]]*/fin-news-agent-app\):[^[:space:]\"']*#\1:${VERSION}#g" \
  "$COMPOSE_FILE" > "$TMP"
sed \
  "s#\(ghcr\.io/[^/[:space:]]*/fin-news-agent-web\):[^[:space:]\"']*#\1:${VERSION}#g" \
  "$TMP" > "$TMP.2"

mv "$TMP.2" "$COMPOSE_FILE"

echo "已更新 $COMPOSE_FILE 的镜像版本 -> ${VERSION}"
grep -n "fin-news-agent" "$COMPOSE_FILE"
