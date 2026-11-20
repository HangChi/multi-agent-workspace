#!/usr/bin/env bash
# PostgreSQL 基础备份（PITR 的起点）。
#
# 为什么需要它：只开 WAL 归档（docker-compose 里的 archive_mode=on）只能重放
# 「归档开始之后」的变更，恢复时必须先有一份**基础备份**作为起点，否则
# WAL 链没有锚点，拼不出一个完整的数据目录。
#
# 产出：
#   $BACKUP_DIR/base-<时间戳>.tar.gz      数据目录（不含 WAL）
#   $BACKUP_DIR/base-<时间戳>-wal.tar.gz  备份期间产生的 WAL（-X stream）
#   $BACKUP_DIR/base-<时间戳>.manifest    恢复起点 LSN / 迁移版本 / 两个包的 sha256
#
# 用法：
#   BACKUP_DIR=/var/backups/aw deploy/backup/pg_basebackup.sh
#
# 建议 cron（每天 02:00，比 pg_dump 早半小时）：
#   0 2 * * * cd /opt/agent-workbench && BACKUP_DIR=/var/backups/aw \
#     deploy/backup/pg_basebackup.sh >> /var/log/aw-basebackup.log 2>&1
set -euo pipefail

PG_CONTAINER="${PG_CONTAINER:-aw-postgres}"
PG_USER="${POSTGRES_USER:-aw}"
PG_DB="${POSTGRES_DB:-agent_workbench}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/aw}"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="${BACKUP_DIR}/base-${STAMP}.tar.gz"
WAL_OUT="${BACKUP_DIR}/base-${STAMP}-wal.tar.gz"
MANIFEST="${BACKUP_DIR}/base-${STAMP}.manifest"
# 容器内暂存目录。不能直接用 stdout：pg_basebackup 明确禁止
# `-Ft` + `-X stream` 写 stdout（cannot stream write-ahead logs in tar mode
# to stdout），必须先落到目录再取回。
TMP_DIR="/tmp/aw-basebackup-${STAMP}"

log() { printf '[base-backup %s] %s\n' "$(date +%H:%M:%S)" "$*"; }

mkdir -p "$BACKUP_DIR"

if ! docker ps --format '{{.Names}}' | grep -qx "$PG_CONTAINER"; then
  log "错误：容器 $PG_CONTAINER 未运行"
  exit 1
fi

# 认证方式说明：官方的 POSTGRES_USER（这里是 aw）初始化时是唯一超级用户，
# **没有** postgres 角色，所以不能靠 peer 认证（OS 用户对不上）。
# 统一走 TCP + 容器内已有的 POSTGRES_PASSWORD，两种初始化方式都能用。
PG_PASSWORD="$(docker exec "$PG_CONTAINER" printenv POSTGRES_PASSWORD 2>/dev/null || true)"
if [ -z "$PG_PASSWORD" ]; then
  log "错误：无法从容器读取 POSTGRES_PASSWORD（基础备份需要密码认证）"
  exit 1
fi

psql_db() {
  docker exec -e PGPASSWORD="$PG_PASSWORD" "$PG_CONTAINER" \
    psql -h 127.0.0.1 -U "$PG_USER" -d "$1" -tAc "$2"
}

# 归档是否真的开着：没开就不要产出「看起来像 PITR 起点」的备份，
# 否则恢复时才发现 WAL 缺口，那时已经晚了。
ARCHIVE_MODE="$(psql_db postgres 'show archive_mode' | tr -d '[:space:]')"
if [ "$ARCHIVE_MODE" != "on" ]; then
  log "错误：archive_mode=${ARCHIVE_MODE}（应为 on）。先确认 compose 的 postgres entrypoint 已生效并重建过容器。"
  exit 1
fi

cleanup() {
  docker exec -u root "$PG_CONTAINER" rm -rf "$TMP_DIR" >/dev/null 2>&1 || true
}
trap cleanup EXIT

log "开始基础备份（容器内 ${TMP_DIR}）"
# -Ft -z      tar + gzip
# -X stream   备份期间的 WAL 单独打包成 pg_wal.tar.gz，避免备份不一致
# -P          进度与端点写 stderr，这里合并捕获：结束点 LSN 是恢复目标，
#             必须用它而不是备份之后的 pg_current_wal_lsn() —— 后者指向
#             「还没归档的将来」，重放到那里会因缺 WAL 段而失败。
BACKUP_LOG="$(docker exec -e PGPASSWORD="$PG_PASSWORD" "$PG_CONTAINER" \
  pg_basebackup -h 127.0.0.1 -U "$PG_USER" -D "$TMP_DIR" -Ft -z -X stream -P 2>&1)"
echo "$BACKUP_LOG" | tail -3

# 输出形如：write-ahead log end point: 0/73000138
END_LSN="$(printf '%s\n' "$BACKUP_LOG" | sed -n 's/.*log end point: \([0-9A-Fa-f/]*\).*/\1/p' | tail -1)"
if [ -z "$END_LSN" ]; then
  log "警告：未能从 pg_basebackup 输出解析结束点 LSN，回退到当前 WAL 位置（恢复目标可能超前）"
  END_LSN="$(psql_db postgres 'select pg_current_wal_lsn()' | tr -d '[:space:]')"
fi

log "取回产物"
docker cp "${PG_CONTAINER}:${TMP_DIR}/base.tar.gz" "$OUT"
docker cp "${PG_CONTAINER}:${TMP_DIR}/pg_wal.tar.gz" "$WAL_OUT"

log "校验 tar 完整性"
tar -tzf "$OUT" > /dev/null
tar -tzf "$WAL_OUT" > /dev/null

RECOVERY_LSN="$END_LSN"
CURRENT_LSN="$(psql_db postgres 'select pg_current_wal_lsn()' | tr -d '[:space:]')"
ALEMBIC_REV="$(psql_db "$PG_DB" 'select version_num from alembic_version' 2>/dev/null | tr -d '[:space:]' || echo 'unknown')"

hash_of() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    sha256sum "$1" | awk '{print $1}'
  fi
}

SHA="$(hash_of "$OUT")"
WAL_SHA="$(hash_of "$WAL_OUT")"
SIZE="$(wc -c < "$OUT" | tr -d ' ')"
WAL_SIZE="$(wc -c < "$WAL_OUT" | tr -d ' ')"

cat > "$MANIFEST" <<EOF
created_at=${STAMP}
container=${PG_CONTAINER}
database=${PG_DB}
recovery_lsn=${RECOVERY_LSN}
current_lsn_at_backup=${CURRENT_LSN}
alembic_version=${ALEMBIC_REV}
file=$(basename "$OUT")
bytes=${SIZE}
sha256=${SHA}
wal_file=$(basename "$WAL_OUT")
wal_bytes=${WAL_SIZE}
wal_sha256=${WAL_SHA}
# 恢复要点（详见 deploy/backup/README.md 的 PITR 一节）：
#   1) 停 postgres，清空数据目录
#   2) 解开 base tar 到数据目录，再解开 -wal tar 到数据目录 pg_wal/
#   3) 写 restore_command='cp /wal-archive/%f %p'
#      + recovery_target_lsn='${RECOVERY_LSN}'（或 recovery_target_time）
#   4) 建 recovery.signal，启动；重放完成后执行 alembic upgrade head
EOF

log "完成：$(basename "$OUT") (${SIZE} 字节) + $(basename "$WAL_OUT") (${WAL_SIZE} 字节)"
log "LSN=${RECOVERY_LSN} 清单：$(basename "$MANIFEST")"

# 保留最近 7 份基础备份（含其 WAL 包与清单）
ls -1t "${BACKUP_DIR}"/base-*.tar.gz 2>/dev/null | grep -v -- '-wal.tar.gz' | tail -n +8 | while read -r old; do
  base="${old%.tar.gz}"
  log "清理旧基础备份：$(basename "$old")"
  rm -f "$old" "${base}-wal.tar.gz" "${base}.manifest"
done
