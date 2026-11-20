#!/usr/bin/env bash
# PITR 恢复演练：把最新基础备份 + WAL 归档恢复到一个**一次性容器**里，
# 断言能起来且数据可读，然后丢弃。不碰生产库、不碰生产卷。
#
# 为什么必须有这个脚本：备份的唯一价值是「能恢复」。只做 base backup 而从不
# 演练，等于把「备份成功了」误当成「恢复可行」——2026-11-17 那次审查里
# pg_dump 的恢复演练就是这个思路，PITR 同样需要一条。
#
# 用法：
#   BACKUP_DIR=/var/backups/aw deploy/backup/pitr_restore_drill.sh
#
# 依赖：基础备份（pg_basebackup.sh）与 WAL 归档卷 aw_pgwal 都在。
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/var/backups/aw}"
PG_CONTAINER="${PG_CONTAINER:-aw-postgres}"
WAL_VOLUME="${WAL_VOLUME:-aw_pgwal}"
DRILL_NAME="aw-pitr-drill-$$"

log() { printf '[pitr-drill %s] %s\n' "$(date +%H:%M:%S)" "$*"; }

LATEST="$(ls -1t "${BACKUP_DIR}"/base-*.tar.gz 2>/dev/null | grep -v -- '-wal.tar.gz' | head -1 || true)"
if [ -z "$LATEST" ]; then
  log "错误：$BACKUP_DIR 下没有 base-*.tar.gz，先跑 pg_basebackup.sh"
  exit 1
fi
MANIFEST="${LATEST%.tar.gz}.manifest"
WAL_FILE="${LATEST%.tar.gz}-wal.tar.gz"
if [ ! -f "$MANIFEST" ]; then
  log "错误：缺少清单 $MANIFEST"
  exit 1
fi
if [ ! -f "$WAL_FILE" ]; then
  log "错误：缺少 WAL 包 $WAL_FILE"
  exit 1
fi

TARGET_LSN="$(grep '^recovery_lsn=' "$MANIFEST" | cut -d= -f2)"
ALEMBIC_EXPECTED="$(grep '^alembic_version=' "$MANIFEST" | cut -d= -f2)"
DB_NAME="$(grep '^database=' "$MANIFEST" | cut -d= -f2)"
# 恢复出来的实例沿用生产库的角色（POSTGRES_USER 初始化时创建的那个），
# 不是 postgres —— 后者只在默认配置下才存在，拿它查询会静默返回空结果。
PG_USER="$(docker exec "$PG_CONTAINER" printenv POSTGRES_USER 2>/dev/null || echo aw)"
BACKUP_ABS="$(cd "$(dirname "$LATEST")" && pwd)"
log "使用备份：$(basename "$LATEST")，目标 LSN=${TARGET_LSN}，期望 alembic=${ALEMBIC_EXPECTED}"

cleanup() {
  docker rm -f "$DRILL_NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# 一次性容器：不挂生产数据卷，只读挂备份目录与 WAL 归档卷。
# entrypoint 里先解包再交给官方 docker-entrypoint.sh —— 它会发现数据目录已存在，
# 跳过 initdb，并在有 recovery.signal 时进入恢复流程。
#
# 恢复目标用 `immediate` 而不是 recovery_lsn，原因是实测踩到的边界：
# pg_basebackup 的结束点常正好落在 WAL 段边界（例如 0/76000000 就是 0/76 段的
# 第一个字节），重放到该点需要打开**段 0/76** —— 而那段在备份时还没创建，
# 归档和 -X stream 里都不会有。结果是 redo 到一致点后报
# `recovery ended before configured recovery target was reached` 并退出。
# `immediate` 表示「到达一致性点就停并 promote」，这正是备份完整可用的证明。
# 要做真正的按时间点恢复，人工演练时把下面两行换成
#   recovery_target_time = '<目标时刻>'
#   recovery_target_action = 'promote'
docker run -d --name "$DRILL_NAME" \
  -v "${BACKUP_ABS}:/backup:ro" \
  -v "${WAL_VOLUME}:/wal-archive:ro" \
  -e PGDATA=/var/lib/postgresql/data \
  -e BASE_TAR="$(basename "$LATEST")" \
  -e WAL_TAR="$(basename "$WAL_FILE")" \
  --entrypoint /bin/sh \
  postgres:16-alpine -c '
    set -e
    mkdir -p "$PGDATA"
    tar xzf "/backup/${BASE_TAR}" -C "$PGDATA"
    # pg_basebackup 的 -X stream 把 WAL 单独打包，内部顶层是 pg_wal/
    tar xzf "/backup/${WAL_TAR}" -C "$PGDATA"
    cat >> "$PGDATA/postgresql.auto.conf" <<EOF
restore_command = '"'"'cp /wal-archive/%f %p'"'"'
recovery_target = '"'"'immediate'"'"'
recovery_target_action = '"'"'promote'"'"'
EOF
    touch "$PGDATA/recovery.signal"
    chown -R postgres:postgres "$PGDATA"
    exec docker-entrypoint.sh postgres
  ' >/dev/null

log "等待恢复完成（最多 90s）"
READY=0
for _ in $(seq 1 45); do
  if docker logs "$DRILL_NAME" 2>&1 | grep -q 'database system is ready to accept connections'; then
    READY=1
    break
  fi
  if [ "$(docker inspect -f '{{.State.Running}}' "$DRILL_NAME" 2>/dev/null)" != "true" ]; then
    break
  fi
  sleep 2
done

if [ "$READY" != "1" ]; then
  log "FAIL：恢复实例未就绪，日志尾部："
  docker logs "$DRILL_NAME" 2>&1 | tail -25
  exit 1
fi

ACTUAL_REV="$(docker exec "$DRILL_NAME" psql -U "$PG_USER" -d "$DB_NAME" -tAc \
  'select version_num from alembic_version' 2>/dev/null | tr -d '[:space:]' || echo '')"
TABLE_COUNT="$(docker exec "$DRILL_NAME" psql -U "$PG_USER" -d "$DB_NAME" -tAc \
  "select count(*) from information_schema.tables where table_schema='public'" 2>/dev/null | tr -d '[:space:]' || echo '0')"
USER_COUNT="$(docker exec "$DRILL_NAME" psql -U "$PG_USER" -d "$DB_NAME" -tAc \
  'select count(*) from users' 2>/dev/null | tr -d '[:space:]' || echo '?')"

log "恢复结果：alembic=${ACTUAL_REV:-未读到} tables=${TABLE_COUNT} users=${USER_COUNT}"

if [ -n "$ALEMBIC_EXPECTED" ] && [ "$ACTUAL_REV" != "$ALEMBIC_EXPECTED" ]; then
  log "FAIL：alembic 版本不符（期望 ${ALEMBIC_EXPECTED}，实际 ${ACTUAL_REV:-空}）"
  exit 1
fi
if [ "$TABLE_COUNT" = "0" ]; then
  log "FAIL：恢复后 public schema 没有表"
  exit 1
fi

log "PASS：基础备份 + WAL 归档可恢复到 LSN=${TARGET_LSN}（临时实例已丢弃）"
