# 备份与恢复演练

两件事必须成对存在：**备份**只有在**恢复演练**通过时才算数。

| 脚本 | 做什么 | 结论 |
|---|---|---|
| `backup.sh` | `pg_dump -Fc` + MinIO `/data` 打包 + manifest（sha256 / 迁移版本 / 表数量）+ 按天清理 | 产出可校验的备份包 |
| `restore-drill.sh` | 把最新备份还原到临时库 `aw_restore_drill`，断言迁移版本与表数量，然后丢弃 | PASS/FAIL，不碰生产库 |

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

- 不做增量/时间点恢复（PITR）。要 PITR 必须开 WAL 归档，属于 DBA 级改造。
- 不备份 `.env` / 密钥。它们不该进备份包（备份目录常被同步到别处），
  请单独用密码管理器或 KMS 保管；恢复时手工提供。
- 不校验对象与数据库行的一致性（例如某条 `artifacts` 记录指向的对象是否真的在 tar 里）。
  这是下一步可做的增强：按 `artifacts.object_key` 逐条 `tar -tz` 比对。

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
