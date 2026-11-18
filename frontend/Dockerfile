# syntax=docker/dockerfile:1
# 前端镜像（T081）：多阶段构建（node 构建 → nginx 托管）、非 root 运行。
#
# 密钥策略：构建期无需任何密钥；API 地址经 nginx 反代到后端（同域），
# 不烧录后端地址/密钥到镜像。

# ---------- Stage 1: 构建 ----------
FROM node:26-alpine AS build

WORKDIR /app

# 先复制锁文件与 package.json，利用层缓存
COPY package.json package-lock.json* ./
# 不加 `|| npm install` 回退：lock 与 package.json 漂移时必须让构建失败，
# 而不是静默解析出一套没验证过的依赖树 —— 后者正是「本地好好的、镜像里不一样」的来源
RUN npm ci

COPY . .
RUN npm run build

# ---------- Stage 2: 运行时（nginx 非 root） ----------
FROM nginx:1.31-alpine AS runtime

# 非 root：nginx 以低权限用户运行
# - 官方 entrypoint 会尝试写 /etc/nginx/conf.d 与 /run/nginx.pid，
#   故自定义 CMD 直接启动 nginx，绕开 entrypoint 脚本
# - /var/cache/nginx（临时文件）与 /run（pid 文件）需可写
RUN apk add --no-cache shadow && \
    groupadd -g 10002 www && \
    useradd -u 10002 -g www -s /usr/sbin/nologin www && \
    mkdir -p /var/cache/nginx /run && \
    chown -R www:www /var/cache/nginx /run && \
    chown -R www:www /usr/share/nginx/html

# 静态产物
COPY --from=build /app/dist /usr/share/nginx/html

# nginx 配置（静态，无模板变量；反代目标为 compose 服务名 api:8000）
COPY nginx.conf /etc/nginx/conf.d/default.conf

# 自定义 CMD：非 root 直接启动 nginx（master 亦为 www 用户）
USER www

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
