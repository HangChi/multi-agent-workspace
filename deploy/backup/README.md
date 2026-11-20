# 备份与恢复演练

两件事必须成对存在：**备份**只有在**恢复演练**通过时才算数。

| 脚本 | 做什么 | 结论 |
|---|---|---|
| `backup.sh` | `pg_dump -Fc` + MinIO `/data` 打包 + manifest（sha256 / 迁移版本 / 表数量）+ 按天清理 | 产出可校验的备份包 |
| `restore-drill.sh` | 把最新备份还原到临时库 `aw_restore_drill`，断言迁移版本与表数量，然后丢弃 | PASS/FAIL，不碰生产库 |
| `pg_basebackup.sh` | `pg_basebackup -Ft -X stream` + 记录 recovery LSN / alembic / sha256 | PITR 的起点（见下节） |

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
```

脚本会先断言 `archive_mode=on`：归档没开就**拒绝产出**——否则会造出一个
「看起来像 PITR 起点、实际拼不出完整数据目录」的备份，问题要到真恢复时才发现。

恢复流程（在目标机上执行，先停服务）：

1. 停 postgres，备份并清空数据目录
2. 解开 `base-*.tar.gz` 到数据目录
3. 写 `postgresql.auto.conf`：
   `restore_command = 'cp /wal-archive/%f %p'`
   `recovery_target_lsn = '<manifest 里的 recovery_lsn>'`（或用 `recovery_target_time`）
4. 建 `recovery.signal`，启动 postgres，等它重放到目标点并转成主库
5. `alembic upgrade head`（若目标时间点早于最新迁移）

**注意**：`pgwal` 是 named volume，`docker compose down -v` 会连归档一起删。
清卷前先确认基础备份已经落到卷外。

## 已知缺口

- 不备份 `.env` / 密钥。它们不该进备份包（备份目录常被同步到别处），
  请单独用密码管理器或 KMS 保管；恢复时手工提供。
- 不校验对象与数据库行的一致性（例如某条 `artifacts` 记录指向的对象是否真的在 tar 里）。
  这是下一步可做的增强：按 `artifacts.object_key` 逐条 `tar -tz` 比对。
- PITR 的**恢复流程没有在本仓库的自动化里演练过**：基础备份脚本会自检归档开关与
  tar 完整性，但「按上面 5 步恢复到指定 LSN」目前是一份需要人工执行的 runbook。
  真机演练前请先在一次性环境里走一遍，别在生产首次使用。

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
