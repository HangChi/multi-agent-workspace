# 审计跟进记录（2026-11-20）

本文只回答一个问题：**2026-11-17 代码审查与 README「已知限制」里挂账的项，
现在各自是什么状态**。逐项可追溯到提交，未闭环的都写了原因。

上游文档：
- [code-audit-2026-11-17.md](code-audit-2026-11-17.md)（13 个编号问题）
- README「已知限制」段（长期挂账的能力缺口）

## 一、已闭环

| 原项 | 处置 | 提交 |
|---|---|---|
| 审计 #3/#4/#5/#6/#7（安全与性能） | 11-17 分支内已修，本轮复查无回归 | — |
| 审计 #10 成本按分取整 | `cost_micros` 已落地并跨重试累计 | 早前 |
| 审计 #12 运行时版本与 CI 不一致 | CI 增加 **Python 3.15 / Node 26 矩阵**，实测 3.15 全量通过 | `879c316` |
| README「错误码两套并存」 | **016 特性**：非相关人统一 404，参与者缺权限仍 403；10 份契约同步 | `de9d77c` |
| README「没有 Grafana 面板」 | 新增 Grafana 服务 + provisioning 看板（10 个 panel） | `475a392` |
| backup README「不做 PITR」 | 开 WAL 归档 + 基础备份 + **恢复演练脚本**（实测恢复 32 表 / 14 用户） | `475a392` `89bcc07` |
| README「尚未实现 Sentry」 | 可选接线：DSN 为空零开销，缺依赖只警告不阻断 | `1101966` |
| 015 T024 视觉回归基线 | 决议暂缓（理由见该 tasks.md），docs 计数修正为 23/24 | `a3b6499` |
| 版本号散落四处 | 收敛为 `app.__version__` 单一来源 + 一致性测试，升 0.2.0 | `2bf37d8` |
| 列表分页无 tie-breaker | agents/requests/groups 排序补 `id.desc()`（**真实翻页重复 bug**） | `a3b6499` |
| **远端 CI 自 11-19 持续失败** | ruff 0.17.6 规则漂移 + uv.lock 未同步，两者均已修并钉版本 | `03be9f1` |

## 二、未闭环（有明确原因）

| 项 | 状态 | 为什么现在不做 |
|---|---|---|
| 3.15 上 2~3 个集成用例偶发失败 | 已缓解（CI 仅 3.15 job 允许 `--reruns 2`） | 单跑必过、全量偶发红，每次失败用例不同。已修掉确认的根因（分页排序），剩余需逐个用 `-p no:randomly`/固定顺序复现，属独立排期 |
| 视觉回归基线（015 T024） | 暂缓 | 需要常驻 Playwright e2e 基建；没有稳定基线时截图 diff 全是噪声 |
| Sentry 实际上报 | 只留接线 | 需要用户提供 DSN 才能验证，且涉及数据留存策略 |
| 真机 PITR 恢复 / 生产部署 smoke | 未做 | 需要有目标主机与域名；容器内一次性实例的恢复演练已通过 |
| 告警阈值调优 | 未做 | 依赖真实流量；阈值是实测基线推的，但没在生产量级下验证 |
| `external-tools/observability` 的多 worker 聚合 | 未做 | 计数器是进程本地，页面已注明只代表单个 worker；全局口径看 Prometheus |

## 三、本轮新增的守卫（防止同类问题复发）

| 守卫 | 位置 | 防什么 |
|---|---|---|
| 版本一致性测试 | `tests/unit/test_version_consistency.py` | 改版本号漏改 pyproject / package.json |
| 响应体同构断言 | `tests/integration/test_error_code_unification.py` | 状态码改对了但 message 泄露存在性 |
| Redis 库隔离 | `tests/conftest.py` `_isolate_test_redis` | 本地开发栈与测试共用 db 0 造成的假红 |
| lint 工具钉版本 | `backend/pyproject.toml` | 补丁版本升级导致「本地绿、CI 红」 |
| 归档权限自检 | `deploy/backup/pg_basebackup.sh` | WAL 归档静默 Permission denied |

## 四、操作纪律（从本轮踩坑提炼）

1. **改 `pyproject.toml` 必须 `uv lock`**：Dockerfile 用 `--locked`，不同步会让
   镜像构建直接失败，而本地 `pip install -e .` 不会有任何提示。
2. **推送后要看 `gh run list`**：本次发现远端 CI 已红一天，本地门禁全绿掩盖了它。
3. **lint 工具用精确版本**：`>=` 让 CI 每次装最新，规则变化会随机弄红提交。
4. 排查「本地绿 CI 红」时，先比对工具版本（`ruff --version` vs CI 日志），
   再怀疑代码。
