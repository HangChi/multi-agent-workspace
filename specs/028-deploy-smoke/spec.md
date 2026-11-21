# Feature Specification: 部署 smoke 与 PITR 恢复演练（028）

**Status**: Implemented（本机验证；目标主机仍不可用）

## 问题陈述

「部署 smoke」与「真机 PITR」一直挂着，原因写的是「无目标主机」——两台服务器
各有生产负载（Lighthouse 跑 JobTrace/blog/1Panel，另一台跑行测），**不能在上面
部署这套栈**：端口冲突、数据污染、以及最现实的一点——出了事我无法负责。

但「没有目标主机」不等于「什么都不做」。可以在本机验证两条最关键的、且与主机无关
的链路：**部署产物能起来并服务**、**备份能真的恢复**。

## 本次交付

### 1. 部署 smoke（`deploy/verify/s23_deploy_smoke.py`）

全栈 restart → 健康 → 迁移到 head → worker 任务注册齐全 → 重跑三条既有验收
（分派通知 / 成员角色 / 邀请申请）。

覆盖的是绝大多数部署事故：镜像漏文件、迁移没跑、环境变量缺失、worker 丢任务注册。

**不覆盖**：空库冷启动（`down -v` 后从零 up）。本机端口写死（5432/6379/9000），
起不了第二套实例；而 `down -v` 会清掉开发库全部数据。空库冷启动的正确场景是
一次性的预发环境——脚本里把这条差异写明了，而不是假装覆盖了。

### 2. PITR 恢复演练（复用既有 `deploy/backup/pitr_restore_drill.sh`）

1. `pg_basebackup.sh`：pg_basebackup + WAL 归档打包 + manifest（含 `recovery_lsn`
   与 `alembic_version`）；
2. `pitr_restore_drill.sh`：把备份 + WAL 恢复到**一次性容器**，断言库能起来、
   alembic 版本对、数据可读，然后丢弃容器。

不碰生产库、不碰生产卷。

## 实测（2026-11-21 本机）

**部署 smoke 7/7**：

| 项 | 结果 |
|---|---|
| 全栈 restart | ✅ |
| `/health` | 200 |
| 迁移到 head | `0026_invite_requests` |
| worker 任务注册 | `execute_run` / `deliver_notification` / `auto_start_request` 全在 |
| s14 通知通道 | 5/5 |
| s15 成员角色 | 4/4 |
| s22 邀请申请 | 4/4 |

**PITR 演练 PASS**：

```
使用备份：base-20261121-201904.tar.gz，目标 LSN=1/33000000
恢复结果：alembic=0026_invite_requests tables=37 users=94
PASS：基础备份 + WAL 归档可恢复到 LSN=1/33000000（临时实例已丢弃）
```

## 仍未做（如实）

- **目标主机上的真实部署**：没有可用主机（理由见上）。要做需要一台干净的机器
  （或子账号 + 独立端口），这是一次需要你确认的资源动作；
- **空库冷启动**：需要预发环境；
- 演练只覆盖「恢复到 base backup 的 LSN」，**没有**做「恢复到一个中间时间点、
  验证某条被删数据回来了」的更细演练——那需要先人为制造一条可识别的变更再删掉。
  当前脚本证明的是「备份 + 归档可恢复」，这是 PITR 的必要条件。
