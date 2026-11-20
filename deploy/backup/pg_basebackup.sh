#!/usr/bin/env bash
# PostgreSQL 基础备份（PITR 的起点）。
#
# 为什么需要它：只开 WAL 归档（docker-compose 里的 archive_mode=on）只能重放
# 「归档开始之后」的变更，恢复时必须先有一份**基础备份**作为起点，否则
# WAL 链没有锚点，pg_basebackup 出来的目录也拼不出一个完整的数据目录。
#
# 产出：
#   $BACKUP_DIR/base-<时间戳>.tar.gz   数据目录（pg_basebackup -Ft -X stream）
#   $BACKUP_DIR/base-<时间戳>.manifest 恢复起点 LSN / 迁移版本 / sha256
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
MANIFEST="${BACKUP_DIR}/base-${STAMP}.manifest"

log() { printf '[base-backup %s] %s\n' "$(date +%H:%M:%S)" "$*"; }

mkdir -p "$BACKUP_DIR"

if ! docker ps --format '{{.Names}}' | grep -qx "$PG_CONTAINER"; then
  log "错误：容器 $PG_CONTAINER 未运行"
  exit 1
fi

# 归档是否真的开着：没开就不要产出「看起来像 PITR 起点」的备份，
# 否则恢复时才发现 WAL 缺口，那时已经晚了。
ARCHIVE_MODE="$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d postgres -tAc 'show archive_mode' | tr -d '[:space:]')"
if [ "$ARCHIVE_MODE" != "on" ]; then
  log "错误：archive_mode=$ARCHIVE_MODE（应为 on）。先确认 compose 的 postgres command 已生效并重启过容器。"
  exit 1
fi

log "开始基础备份 → $OUT"
# -D -           流式写到 stdout（-Ft 单 tar 才允许）
# -X stream      同时流式传 WAL，备份期间的数据变更不会丢
# -z             压缩；大库可去掉 -z 换更快的速度
docker exec -i "$PG_CONTAINER" \
  pg_basebackup -U "$PG_USER" -D - -Ft -X stream -z > "$OUT"

log "校验 tar 完整性"
tar -tzf "$OUT" > /dev/null

RECOVERY_LSN="$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d postgres -tAc 'select pg_current_wal_lsn()' | tr -d '[:space:]')"
ALEMBIC_REV="$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -tAc 'select version_num from alembic_version' 2>/dev/null | tr -d '[:space:]' || echo 'unknown')"
SHA="$(shasum -a 256 "$OUT" 2>/dev/null | awk '{print $1}' || sha256sum "$OUT" | awk '{print $1}')"
SIZE="$(wc -c < "$OUT" | tr -d ' ')"

cat > "$MANIFEST" <<EOF
created_at=${STAMP}
container=${PG_CONTAINER}
database=${PG_DB}
recovery_lsn=${RECOVERY_LSN}
alembic_version=${ALEMBIC_REV}
file=$(basename "$OUT")
bytes=${SIZE}
sha256=${SHA}
# 恢复要点：
#   1) 停 postgres，清空数据目录
#   2) 解包此 tar 到数据目录
#   3) 写 recovery.signal + restore_command='cp /wal-archive/%f %p'
#      + recovery_target_lsn='${RECOVERY_LSN}'
#   4) 启动后执行 alembic upgrade head（若目标 LSN 早于最新迁移）
EOF

log "完成：$(basename "$OUT") (${SIZE} 字节, LSN=${RECOVERY_LSN})"
log "清单：$(basename "$MANIFEST")"

# 保留最近 7 份基础备份，避免归档卷无限增长
ls -1t "${BACKUP_DIR}"/base-*.tar.gz 2>/dev/null | tail -n +8 | while read -r old; do
  log "清理旧基础备份：$(basename "$old")"
  rm -f "$old" "${old%.tar.gz}.manifest"
done
