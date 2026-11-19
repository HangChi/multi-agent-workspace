#!/usr/bin/env bash
# 切回多容器模式：api / worker / beat 各自独立（可单独扩 worker）。
#
# 与 single.sh 对称：先停掉 aw-app（否则它与 api 同时消费 Celery 队列、
# 同时跑 beat，会出现「建单两次」「同一 cron 触发两次」）。
set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE=(docker compose -p aw -f deploy/docker-compose.yml)
unset DATABASE_URL || true

log() { printf '[multi %s] %s\n' "$(date +%H:%M:%S)" "$*"; }

if docker ps -a --format '{{.Names}}' | grep -qx aw-app; then
  log "移除单容器 app（否则会重复消费队列/重复触发 cron）"
  docker rm -f aw-app >/dev/null
fi

log "重建并启动 api / worker / beat / frontend"
"${COMPOSE[@]}" up -d --build api worker beat
docker rm -f aw-frontend >/dev/null 2>&1 || true
"${COMPOSE[@]}" up -d --no-deps frontend

log "当前容器："
docker ps --filter name=aw- --format '  {{.Names}}\t{{.Status}}' | sort
