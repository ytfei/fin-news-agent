# fin-news-v5 前端镜像：构建 React 静态资源 + nginx 托管 + /api 反代
# 构建：docker build -t fin-news-web ./web

# ============ 构建阶段 ============
# pnpm 11 要求 Node >= 22.13（node:sqlite 内置模块），故用 node:22
FROM node:22-alpine AS build
WORKDIR /web

# 必须带上 pnpm-workspace.yaml：pnpm 11 的 onlyBuiltDependencies(esbuild)
# 只从该文件读取，缺失会导致 esbuild 构建脚本被忽略、vite build 失败
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN corepack enable && pnpm install --frozen-lockfile

COPY . .
RUN pnpm build

# ============ 运行阶段 ============
FROM nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /web/dist /usr/share/nginx/html

EXPOSE 80
