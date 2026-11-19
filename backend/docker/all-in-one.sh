#!/usr/bin/env bash
# 单容器模式：API + Celery worker + Celery beat 跑在同一个容器里。
#
# 为什么提供它：多容器模式下 api/worker/beat 各自构建、各自重启，很容易出现
# 「api 已经是新代码、worker 还是四小时前的镜像」——2026-11-19 实测就撞上了
# （worker/beat 跑的是悬空镜像 ID，与 api 差一代）。合成一个容器后，三个进程
# 共享同一份镜像，版本漂移在物理上不可能发生。
#
# 代价（不是免费的，见 deploy/README 的说明）：
# 1. 必须统一 DB_POOL=null。worker 用 asyncio.run 每次新建事件循环，连接池跨
#    loop 复用会报错；API 用 NullPool 意味着每请求新建连接，吞吐低于连接池。
#    单节点自用场景可以接受，高并发场景应该维持多容器。
# 2. 不能在容器内单独扩 worker：scale 会连带复制 beat，导致同一 cron 被多个
#    beat 重复触发。
#
# 进程管理：不引入 supervisor，用 bash 的 wait -n——任一进程退出就让容器退出，
# 交给 restart: unless-stopped 整体重启。这样「半个系统还活着」的状态不存在。
set -uo pipefail

WORKER_CONCURRENCY="${WORKER_CONCURRENCY:-2}"
LOG_LEVEL="${LOG_LEVEL:-info}"

log() { printf '[all-in-one %s] %s\n' "$(date +%H:%M:%S)" "$*"; }

log "启动 uvicorn / celery worker / celery beat（同一镜像：进程间不可能版本漂移）"

uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2 --no-access-log &
api_pid=$!

celery -A app.tasks.celery_app:celery_app worker \
  --loglevel="$LOG_LEVEL" --concurrency="$WORKER_CONCURRENCY" &
worker_pid=$!

celery -A app.tasks.celery_app:celery_app beat --loglevel="$LOG_LEVEL" &
beat_pid=$!

shutdown() {
  log "收到退出信号，停止全部子进程"
  kill -TERM "$api_pid" "$worker_pid" "$beat_pid" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap shutdown TERM INT

# wait -n：任一进程退出即返回，它的退出码决定容器退出码（让 restart 策略生效）
wait -n
status=$?
log "有进程退出（status=$status），容器随之退出以便整体重启"
shutdown
exit "$status"
