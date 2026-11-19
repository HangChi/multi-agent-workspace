#!/usr/bin/env bash
# 单容器模式启动：api + worker + beat 合成一个 app 容器。
#
# 为什么需要脚本而不是一条 up：frontend 的 depends_on 指向 api，直接
# `up frontend` 会把 api 也拉起来，于是 app 与 api 同时跑两份 uvicorn
# （重复消费 Celery 队列、重复建单）。所以 frontend 必须用 --no-deps 单独起。
#
# 用法：
#   deploy/single.sh            # 切到单容器模式
#   deploy/single.sh status     # 看当前跑的是哪种模式
#
# 切回多容器：deploy/multi.sh（或 docker compose up -d api worker beat，并停 app）
set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE=(docker compose -p aw -f deploy/docker-compose.yml)
# 部署前清掉环境里的 DATABASE_URL：它会覆盖 .env 的变量替换（见 deploy/README）
unset DATABASE_URL || true

log() { printf '[single %s] %s\n' "$(date +%H:%M:%S)" "$*"; }

case "${1:-up}" in
  status)
    docker ps --filter name=aw- --format '{{.Names}}\t{{.Status}}' | sort
    if docker ps --format '{{.Names}}' | grep -qx aw-app; then
      echo "模式：单容器（aw-app）"
    else
      echo "模式：多容器（api/worker/beat 分离）"
    fi
    exit 0
    ;;
  up) ;;
  *)
    echo "用法：$0 [up|status]" >&2
    exit 2
    ;;
esac

log "停止多容器模式下的 api/worker/beat"
"${COMPOSE[@]}" stop api worker beat >/dev/null 2>&1 || true

log "重建并启动 app（含依赖 postgres/redis/minio/migrate/dns-proxy）"
"${COMPOSE[@]}" --profile single up -d --build postgres redis minio migrate dns-proxy app

log "启动 frontend（--no-deps：避免把 api 一起拉起来）"
# 手工 docker run 起过的同名容器不归 compose 管，compose 会因为名字冲突拒绝创建；
# frontend 是无状态 nginx，直接移除再交给 compose 重建最省事。
docker rm -f aw-frontend >/dev/null 2>&1 || true
"${COMPOSE[@]}" up -d --no-deps frontend

log "等待 app 就绪（/health/ready）"
for _ in $(seq 1 40); do
  state=$(docker inspect -f '{{.State.Health.Status}}' aw-app 2>/dev/null || echo starting)
  [ "$state" = "healthy" ] && break
  sleep 3
done
state=$(docker inspect -f '{{.State.Health.Status}}' aw-app 2>/dev/null || echo unknown)
if [ "$state" != "healthy" ]; then
  echo "[single] app 未就绪（$state）。最近日志：" >&2
  docker logs --tail 40 aw-app >&2 || true
  exit 1
fi

log "完成。当前容器："
docker ps --filter name=aw- --format '  {{.Names}}\t{{.Status}}' | sort
log "回滚到多容器：${COMPOSE[*]} up -d api worker beat && docker rm -f aw-app"
