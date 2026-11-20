# 备份与恢复演练

两件事必须成对存在：**备份**只有在**恢复演练**通过时才算数。

| 脚本 | 做什么 | 结论 |
|---|---|---|
| `backup.sh` | `pg_dump -Fc` + MinIO `/data` 打包 + manifest（sha256 / 迁移版本 / 表数量）+ 按天清理 | 产出可校验的备份包 |
| `restore-drill.sh` | 把最新备份还原到临时库 `aw_restore_drill`，断言迁移版本与表数量，然后丢弃 | PASS/FAIL，不碰生产库 |
| `pg_basebackup.sh` | `pg_basebackup -Ft -z -X stream` + 记录恢复 LSN / alembic / 两个包的 sha256 | PITR 的起点（见下节） |
| `pitr_restore_drill.sh` | 把最新基础备份 + WAL 归档恢复到一次性容器，断言 alembic 版本与表数，然后丢弃 | PASS/FAIL，不碰生产库 |

## 定时执行（宿主机 cron）

备份脚本通过 `docker exec` 操作容器，因此跑在**宿主机**上，不进容器：

```cron
# 每天 02:30 备份，保留 14 天
30 2 * * * cd /opt/agent-workbench && BACKUP_DIR=/var/backups/aw deploy/backup/backup.sh >> /var/log/aw-backup.log 2>&1
# 每周日 03:30 恢复演练（用上一份备份验证可恢复性）
30 3 * * 0 cd /opt/agent-workbench && BACKUP_DIR=/var/backups/aw deploy/backup/restore-drill.sh >> /var/log/aw-restore-drill.log 2>&1
```

演练失败要有感知：把第二行的输出接到你的告警渠道（或让 cron 的 `MAILTO` 指向邮箱）。
只写日志不看的演练等于没做。

## 覆盖范围与已知缺口

**覆盖**：PostgreSQL 全库（含 `alembic_version`，可直接 `alembic upgrade head` 续迁移）、
MinIO 对象数据目录（附件、沙箱制品）。

**不做**：

## 时间点恢复（PITR）

`pg_dump` 只能恢复到「上一次备份的那一刻」。PITR 能恢复到任意一个 WAL 位置
（例如「误删数据的前 1 秒」），代价是两块东西必须同时存在：

| 组成 | 由谁负责 | 位置 |
|---|---|---|
| WAL 归档 | `docker-compose.yml` 里 postgres 的 `archive_mode=on` + `archive_command` | `pgwal` 卷（容器内 `/wal-archive`） |
| 基础备份 | `pg_basebackup.sh` | `$BACKUP_DIR/base-*.tar.gz` + `.manifest` |

```bash
# 每天 02:00 做基础备份（比 pg_dump 早半小时）
0 2 * * * cd /opt/agent-workbench && BACKUP_DIR=/var/backups/aw \
  deploy/backup/pg_basebackup.sh >> /var/log/aw-basebackup.log 2>&1
# 每周日 04:00 演练恢复（用最新基础备份验证「真的能恢复」）
0 4 * * 0 cd /opt/agent-workbench && BACKUP_DIR=/var/backups/aw \
  deploy/backup/pitr_restore_drill.sh >> /var/log/aw-pitr-drill.log 2>&1
```

脚本会先断言 `archive_mode=on`：归档没开就**拒绝产出**——否则会造出一个
「看起来像 PITR 起点、实际拼不出完整数据目录」的备份，问题要到真恢复时才发现。

### 恢复演练（推荐先跑这个，再谈生产恢复）

`pitr_restore_drill.sh` 起一个一次性容器，把最新备份恢复进去并断言数据可用：

```bash
BACKUP_DIR=/var/backups/aw deploy/backup/pitr_restore_drill.sh
# [pitr-drill] 恢复结果：alembic=0016_invite_code_revoked tables=32 users=14
# [pitr-drill] PASS：基础备份 + WAL 归档可恢复到 LSN=0/76000000（临时实例已丢弃）
```

它用 `recovery_target = 'immediate'`（到一致点就停并 promote），而不是重放到
manifest 里的 `recovery_lsn`。原因是一次实测踩到的边界：备份结束点常正好落在
WAL 段边界（`0/76000000` 就是 `0/76` 段的第一个字节），重放到它需要段 `0/76`，
而那段在备份时尚未创建 —— 归档和 `-X stream` 里都不会有，恢复会以
`recovery ended before configured recovery target was reached` 退出。

**按时间点恢复**（人工，目标机执行）：把 drill 脚本里那两行换成
`recovery_target_time = '<目标时刻>'` 与 `recovery_target_action = 'promote'`，
其余步骤一致。目标时刻必须落在归档覆盖范围内。

生产手工恢复 5 步：停服务 → 解 base 与 wal 两个包到数据目录 → 写
`restore_command` 与恢复目标 → 建 `recovery.signal` 启动 → `alembic upgrade head`。

**注意**：`pgwal` 是 named volume，`docker compose down -v` 会连归档一起删。
清卷前先确认基础备份已经落到卷外。

## 已知缺口

- 不备份 `.env` / 密钥。它们不该进备份包（备份目录常被同步到别处），
  请单独用密码管理器或 KMS 保管；恢复时手工提供。
- 不校验对象与数据库行的一致性（例如某条 `artifacts` 记录指向的对象是否真的在 tar 里）。
  这是下一步可做的增强：按 `artifacts.object_key` 逐条 `tar -tz` 比对。
- PITR 的**真机恢复**仍未在目标主机上做过：脚本恢复的是容器内的一次性实例，
  验证了「备份+归档链可恢复」，但没有验证「在真实主机上停服务、换数据目录」这一段。

## 实测记录（2026-11-20，PITR）

```
[base-backup] 完成：base-20261120-220455.tar.gz (7127760 字节) + -wal.tar.gz (17077 字节)
[base-backup] LSN=0/76000000 清单：base-20261120-220455.manifest
[pitr-drill] 恢复结果：alembic=0016_invite_code_revoked tables=32 users=14
[pitr-drill] PASS：基础备份 + WAL 归档可恢复到 LSN=0/76000000（临时实例已丢弃）
```

同时验证了 WAL 归档链路：`pg_switch_wal()` 后 `/wal-archive` 落盘 4 个 16 MB 段，
`pg_stat_archiver.archived_count=4`、修复权限后无新增失败。

## 实测记录（2026-11-17，开发机 Docker Desktop）

```
[backup] dump 校验通过（316K）
[backup] 对象打包完成（84K）
[backup] manifest: alembic_revision=0007_schedules table_count=26
[drill]  还原完成
[drill]  alembic_revision=0007_schedules tables=26 users=81 runs=394
[drill]  PASS：备份可恢复（临时库已丢弃，未触碰生产库）
```

对象快照大小远小于数据库是正常的：开发库里沙箱制品大多已被清理任务回收。
