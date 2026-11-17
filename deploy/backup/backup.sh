#!/usr/bin/env bash
# 备份：PostgreSQL 逻辑备份 + 对象存储（MinIO）数据目录快照。
#
# 设计取舍：
# - 数据库用 `pg_dump -Fc`（自定义格式）：可选择性恢复、自带压缩、`pg_restore -l` 能校验完整性。
#   直接 tar 数据目录在运行中是不安全的（PG 的文件不全是一个事务的视图）。
# - 对象存储用 tar 打 `/data`：MinIO 的对象是不可变的，tar 是可接受的快照手段；
#   真正的强一致备份应配 `mc mirror`，但那要额外装客户端，这里先保证「有备份、能验证」。
# - 每个备份都写 manifest（大小 + sha256 + 迁移版本）：恢复演练脚本据此校验，
#   避免出现「文件在、内容是空的」这种最坏的备份。
#
# 用法：
#   deploy/backup/backup.sh                 # 备份到 ./backups
#   BACKUP_DIR=/var/backups/aw deploy/backup/backup.sh
#   BACKUP_OBJECTS=0 deploy/backup/backup.sh   # 只备份数据库（对象太大时）
set -euo pipefail

PG_CONTAINER="${PG_CONTAINER:-aw-postgres}"
MINIO_CONTAINER="${MINIO_CONTAINER:-aw-minio}"
PGUSER="${PGUSER:-aw}"
PGDATABASE="${PGDATABASE:-agent_workbench}"
BACKUP_DIR="${BACKUP_DIR:-$(cd "$(dirname "$0")/../.." && pwd)/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
BACKUP_OBJECTS="${BACKUP_OBJECTS:-1}"

ts="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_DIR"

log() { printf '[backup %s] %s\n' "$(date +%H:%M:%S)" "$*"; }
die() { printf '[backup] ERROR: %s\n' "$*" >&2; exit 1; }

command -v docker >/dev/null || die "docker 未安装"

docker inspect -f '{{.State.Running}}' "$PG_CONTAINER" 2>/dev/null | grep -q true \
  || die "容器 $PG_CONTAINER 未运行"

db_file="$BACKUP_DIR/db-$ts.dump"
log "pg_dump $PGDATABASE -> $db_file"
docker exec "$PG_CONTAINER" pg_dump -U "$PGUSER" -d "$PGDATABASE" -Fc >"$db_file.tmp"
mv "$db_file.tmp" "$db_file"

# 完整性校验：pg_restore -l 能列出目录才算 dump 可读（空文件/截断会在这里失败）
docker exec -i "$PG_CONTAINER" pg_restore -l <"$db_file" >/dev/null \
  || die "dump 不可读（pg_restore -l 失败）"
log "dump 校验通过（$(du -h "$db_file" | cut -f1)）"

objects_file=""
if [ "$BACKUP_OBJECTS" = "1" ]; then
  if docker inspect -f '{{.State.Running}}' "$MINIO_CONTAINER" >/dev/null 2>&1; then
    objects_file="$BACKUP_DIR/objects-$ts.tar.gz"
    log "打包对象存储 $MINIO_CONTAINER:/data -> $objects_file"
    # 用镜像自带的 python？没有 tar？python:3.12-slim 自带 tar（busybox 没有，slim 有 gnu tar）
    docker exec "$MINIO_CONTAINER" tar -C /data -czf - . >"$objects_file"
    log "对象打包完成（$(du -h "$objects_file" | cut -f1)）"
  else
    log "跳过对象备份：容器 $MINIO_CONTAINER 不存在或未运行"
  fi
fi

# manifest：恢复演练与人工排查都依赖它
manifest="$BACKUP_DIR/manifest-$ts.txt"
{
  echo "created_at=$ts"
  echo "database=$PGDATABASE"
  echo "db_dump=$(basename "$db_file")"
  echo "db_sha256=$(sha256sum "$db_file" | cut -d' ' -f1)"
  echo "db_bytes=$(wc -c <"$db_file" | tr -d ' ')"
  [ -n "$objects_file" ] && echo "objects_tar=$(basename "$objects_file")"
  [ -n "$objects_file" ] && echo "objects_sha256=$(sha256sum "$objects_file" | cut -d' ' -f1)"
  echo "alembic_revision=$(docker exec "$PG_CONTAINER" psql -U "$PGUSER" -d "$PGDATABASE" -Atc 'select version_num from alembic_version' 2>/dev/null || echo unknown)"
  echo "table_count=$(docker exec "$PG_CONTAINER" psql -U "$PGUSER" -d "$PGDATABASE" -Atc "select count(*) from information_schema.tables where table_schema='public'" 2>/dev/null || echo unknown)"
} >"$manifest"
log "manifest: $manifest"

# 保留策略：按天清理，避免磁盘被历史备份吃满
if [ "$RETENTION_DAYS" -gt 0 ]; then
  deleted=$(find "$BACKUP_DIR" -maxdepth 1 -type f \
    \( -name 'db-*.dump' -o -name 'objects-*.tar.gz' -o -name 'manifest-*.txt' \) \
    -mtime +"$RETENTION_DAYS" -print -delete | wc -l | tr -d ' ')
  log "清理超过 ${RETENTION_DAYS} 天的备份：$deleted 个文件"
fi

log "完成。恢复演练：deploy/backup/restore-drill.sh"
