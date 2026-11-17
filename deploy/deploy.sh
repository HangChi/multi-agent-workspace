#!/usr/bin/env bash
# 生产部署：构建 → 迁移 → 换容器 → 健康门禁 → 失败回滚。
#
# 设计取舍：
# - **先迁移后换服务**：`migrate` 是一次性容器，跑完才允许新代码接管。
#   反过来做会出现「新代码读到旧表结构」的窗口。
# - **迁移不做回滚**：数据库降级会丢数据，脚本不替你做这个决定。
#   所以要求迁移本身向后兼容（加列先给默认值，删列留到下一版）。
# - **健康门禁是唯一判据**：容器起来了不等于能服务。只看 `/api/v1/health/ready`
#   （它同时探 DB 与 Redis），不通过就回滚。
# - **回滚靠本地镜像 tag**：部署前把当前镜像打成 `:previous`，失败时重新 tag 回去。
#   不依赖 registry 拉旧版本，也不依赖 git 回到旧 commit。
#
# 用法：
#   deploy/deploy.sh                 # 全量部署
#   SERVICES="api worker" deploy/deploy.sh
#   HEALTH_TIMEOUT=180 deploy/deploy.sh
#   deploy/deploy.sh --check         # 只做前置检查，不动容器
set -euo pipefail

APP_DIR="${APP_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
COMPOSE_FILE="${COMPOSE_FILE:-deploy/docker-compose.yml}"
PROJECT="${PROJECT:-aw}"
SERVICES="${SERVICES:-api worker beat frontend}"
FRONTEND_PORT="${FRONTEND_PORT:-8080}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-120}"
HEALTH_URL="http://127.0.0.1:${FRONTEND_PORT}/api/v1/health/ready"
CHECK_ONLY=0

[ "${1:-}" = "--check" ] && CHECK_ONLY=1

log() { printf '[deploy %s] %s\n' "$(date +%H:%M:%S)" "$*"; }
die() { printf '[deploy] FAIL: %s\n' "$*" >&2; exit 1; }

compose() { docker compose -p "$PROJECT" -f "$COMPOSE_FILE" "$@"; }

# ---------- 1. 前置检查（--check 也走这一段）----------
preflight() {
  command -v docker >/dev/null || die "docker 未安装"
  docker compose version >/dev/null 2>&1 || die "docker compose (v2) 不可用"
  [ -f "$COMPOSE_FILE" ] || die "找不到 $COMPOSE_FILE"
  [ -f "$(dirname "$COMPOSE_FILE")/.env" ] || die "缺少 $(dirname "$COMPOSE_FILE")/.env（生产密钥不走 git）"
  compose config >/dev/null || die "compose 配置解析失败"
  log "前置检查通过（project=$PROJECT, services=${SERVICES}）"
}

if [ "$CHECK_ONLY" = "1" ]; then
  preflight
  log "--check 完成，未改动任何容器"
  exit 0
fi

cd "$APP_DIR"
preflight

# ---------- 2. 记录当前镜像，供回滚使用 ----------
service_image_ref() {
  # compose 默认镜像名是 `<project>-<service>`；`compose images -q` 在某些 compose
  # 版本下返回的是 dangling 的 sha256，直接 docker tag 会报 "No such image"。
  local service="$1" ref
  ref="${PROJECT}-${service}"
  if docker image inspect "$ref" >/dev/null 2>&1; then
    printf '%s' "$ref"
    return 0
  fi
  ref="$(docker inspect -f '{{.Config.Image}}' "${PROJECT}-${service}" 2>/dev/null || true)"
  if [ -n "$ref" ] && docker image inspect "$ref" >/dev/null 2>&1; then
    printf '%s' "$ref"
    return 0
  fi
  return 1
}

previous=()
for service in $SERVICES; do
  if ref="$(service_image_ref "$service")"; then
    docker tag "$ref" "${PROJECT}-${service}:previous"
    previous+=("$service")
    log "已保存回滚点：${PROJECT}-${service}:previous ← $ref"
  else
    log "跳过回滚点：$service 当前没有可用镜像（首次部署）"
  fi
done

rollback() {
  log "开始回滚..."
  local service
  for service in "${previous[@]:-}"; do
    [ -n "$service" ] || continue
    docker tag "${PROJECT}-${service}:previous" "${PROJECT}-${service}" || true
  done
  # --no-build/--pull never：回滚只能用本地已有的镜像，绝不能在回滚路径上再连网
  compose up -d --no-build --pull never --no-deps $SERVICES || true
  log "已回滚到部署前的镜像。数据库迁移**不会**自动降级，请人工确认。"
}

on_error() {
  local code=$?
  trap - ERR
  printf '[deploy] 部署中断（exit=%s）\n' "$code" >&2
  rollback
  exit "$code"
}
trap on_error ERR

# ---------- 3. 构建 ----------
if [ "${SKIP_BUILD:-0}" = "1" ]; then
  # 代码没变只想重启，或镜像已由 CI 构建好：跳过构建直接用本地镜像
  log "SKIP_BUILD=1，跳过构建，直接使用本地镜像"
else
  log "构建镜像：$SERVICES + migrate"
  compose build $SERVICES migrate
fi

# ---------- 4. 迁移（一次性容器，跑完即退）----------
log "执行数据库迁移"
compose up -d --no-deps migrate
# 等它退出：compose 的 migrate 是一次性服务
for _ in $(seq 1 60); do
  state="$(docker inspect -f '{{.State.Status}}' "aw-migrate" 2>/dev/null || echo missing)"
  [ "$state" = "exited" ] && break
  sleep 2
done
exit_code="$(docker inspect -f '{{.State.ExitCode}}' "aw-migrate" 2>/dev/null || echo 1)"
[ "$exit_code" = "0" ] || die "迁移失败（exit=$exit_code），日志：docker logs aw-migrate"
log "迁移完成"

# ---------- 5. 换服务 ----------
log "启动服务：$SERVICES"
compose up -d --no-deps $SERVICES

# ---------- 6. 健康门禁 ----------
log "健康检查：${HEALTH_URL}（最长 ${HEALTH_TIMEOUT}s）"
deadline=$((SECONDS + HEALTH_TIMEOUT))
until curl -fsS --max-time 3 "$HEALTH_URL" >/dev/null 2>&1; do
  if [ "$SECONDS" -ge "$deadline" ]; then
    printf '\n' >&2
    compose ps >&2 || true
    docker logs --tail 40 aw-api >&2 || true
    die "健康检查超时，判定部署失败"
  fi
  sleep 3
done
log "健康检查通过"

# worker/beat 不在 HTTP 路径上，单独确认它们没在崩溃重启
for service in worker beat; do
  case " $SERVICES " in
    *" $service "*) ;;
    *) continue ;;
  esac
  container="aw-$service"
  status="$(docker inspect -f '{{.State.Status}}' "$container" 2>/dev/null || echo missing)"
  restarts="$(docker inspect -f '{{.RestartCount}}' "$container" 2>/dev/null || echo 0)"
  log "$service: status=$status restarts=$restarts"
  [ "$status" = "running" ] || die "$service 未处于 running 状态"
  [ "$restarts" -le 1 ] || die "$service 连续重启（$restarts 次），疑似启动即崩"
done

trap - ERR
log "部署完成。回滚点仍保留为 aw-<service>:previous"
