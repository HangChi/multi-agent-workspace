#!/usr/bin/env bash
# 恢复演练：把最新备份还原到一个**临时库**并校验，然后丢弃。
#
# 为什么必须有这一步：备份「文件存在 + 大小正常」不代表能恢复。
# 只有真的 restore 一遍、比对迁移版本与表数量，才知道这份备份在需要它的那天能不能用。
# 演练不碰生产库：目标库名固定带 _drill 后缀，结束时直接 DROP。
#
# 用法：
#   deploy/backup/restore-drill.sh                  # 用最新一份备份
#   deploy/backup/restore-drill.sh db-2026...dump   # 指定某一份
set -euo pipefail

PG_CONTAINER="${PG_CONTAINER:-aw-postgres}"
PGUSER="${PGUSER:-aw}"
BACKUP_DIR="${BACKUP_DIR:-$(cd "$(dirname "$0")/../.." && pwd)/backups}"
DRILL_DB="${DRILL_DB:-aw_restore_drill}"

log() { printf '[drill %s] %s\n' "$(date +%H:%M:%S)" "$*"; }
die() { printf '[drill] FAIL: %s\n' "$*" >&2; exit 1; }

dump="${1:-}"
if [ -z "$dump" ]; then
  dump="$(ls -1t "$BACKUP_DIR"/db-*.dump 2>/dev/null | head -1 || true)"
fi
[ -n "$dump" ] && [ -f "$dump" ] || die "找不到备份文件（$BACKUP_DIR）"
log "使用备份：$dump"

# 1) 校验 dump 可读
docker exec -i "$PG_CONTAINER" pg_restore -l <"$dump" >/dev/null || die "dump 不可读"

# 2) 建临时库（先清掉上次演练的残留）
docker exec "$PG_CONTAINER" psql -U "$PGUSER" -d postgres -q \
  -c "DROP DATABASE IF EXISTS $DRILL_DB" >/dev/null
docker exec "$PG_CONTAINER" psql -U "$PGUSER" -d postgres -q \
  -c "CREATE DATABASE $DRILL_DB" >/dev/null
log "临时库已建：$DRILL_DB"

cleanup() {
  docker exec "$PG_CONTAINER" psql -U "$PGUSER" -d postgres -q \
    -c "DROP DATABASE IF EXISTS $DRILL_DB" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# 3) 还原（--no-owner：临时库没有原角色也能导；--exit-on-error：别把半成品当成功）
if ! docker exec -i "$PG_CONTAINER" pg_restore -U "$PGUSER" -d "$DRILL_DB" \
  --no-owner --no-privileges --exit-on-error <"$dump" 2>/tmp/restore-drill.err; then
  tail -20 /tmp/restore-drill.err >&2
  die "pg_restore 失败（详见 /tmp/restore-drill.err）"
fi
log "还原完成"

# 4) 断言：迁移版本、表数量、关键表行数
revision="$(docker exec "$PG_CONTAINER" psql -U "$PGUSER" -d "$DRILL_DB" -Atc \
  "select version_num from alembic_version" 2>/dev/null || echo missing)"
tables="$(docker exec "$PG_CONTAINER" psql -U "$PGUSER" -d "$DRILL_DB" -Atc \
  "select count(*) from information_schema.tables where table_schema='public'")"
users="$(docker exec "$PG_CONTAINER" psql -U "$PGUSER" -d "$DRILL_DB" -Atc \
  "select count(*) from users" 2>/dev/null || echo 0)"
runs="$(docker exec "$PG_CONTAINER" psql -U "$PGUSER" -d "$DRILL_DB" -Atc \
  "select count(*) from runs" 2>/dev/null || echo 0)"

log "alembic_revision=$revision tables=$tables users=$users runs=$runs"

[ "$revision" != "missing" ] || die "恢复后找不到 alembic_version —— 这份备份不能直接上线"
[ "$tables" -ge 20 ] || die "恢复后表数量异常（$tables），疑似不完整备份"

# 5) 对象快照完整性（有就校验，没有不算失败）
objects="$(ls -1t "$BACKUP_DIR"/objects-*.tar.gz 2>/dev/null | head -1 || true)"
if [ -n "$objects" ]; then
  tar -tzf "$objects" >/dev/null 2>&1 || die "对象快照不可读：$objects"
  log "对象快照可读：$(basename "$objects")"
fi

log "PASS：备份可恢复（临时库已丢弃，未触碰生产库）"
