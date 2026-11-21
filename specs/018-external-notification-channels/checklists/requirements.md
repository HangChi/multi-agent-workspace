# Requirements Checklist: 018 外部通知通道

## Spec 质量核对

| 条目 | 状态 | 说明 |
|---|---|---|
| 每个 US 独立可测 | ✅ | US1 配置 / US2 签名投递 / US3 失败可见 / US4 退订 / US5 SSRF 拒绝 |
| FR 有验收锚点 | ✅ | FR-001~010 对应 s14 的 A/B/C/D 组与 e2e 两例 |
| 边界单独成节 | ✅ | spec 边界在 US5 与错误矩阵；`kind: email` 走 422 而不是静默保存 |
| 「不含」每项有依据 | ✅ | 邮件/SMS、聚合、频率限制、订阅表达式（research R-01/R-03/R-06） |
| 未决事项 ≤3 | ✅ | 0 个 |
| SC 可判定 | ✅ | SC-001~005 全部在真实栈跑过 |

## 与宪法（v1.1.0）对齐

- [x] I. SDD+TDD：规格先行，测试与实现同批提交
- [x] I. 未实测不勾：T405（合并）保持未勾直到真的合并
- [x] II. 范围边界：四项排除写进 spec，不在实现中途扩张
- [x] III. 完整系统：配置/投递/记录/退订/前端/验收齐全
- [x] IV. 质量门禁：ruff / format / mypy / pytest / eslint / tsc / vitest 全绿
- [x] 依赖零新增；SSRF 判定与 002 同源（不是复制一份）

## 与既有契约对齐

- [x] **002**：`read_url` 的 `FetchError` 语义与 `_MAX_REDIRECTS` 行为不变
      （只把判定函数移到 `core/net_guard`，71 例 read_url 测试全绿）
- [x] **007**：站内通知链路不变；`create_notification` 只增加一次「入队」调用，
      TESTING 短路
- [x] **016**：别人的通道 404、viewer 403
- [x] **017 的教训**：`kind` 用 varchar + CHECK，不用枚举（加类型不用迁移）

## 实测回填（2026-11-21）

- [x] 后端：新增 **29 例**（17 单测 + 12 集成），全量 **2053 passed**，覆盖率 **94.62%**
- [x] 前端：新增 **2 例**，全量 **150 passed / 28 files**
- [x] e2e：新增 **2 例**，全量 **19 passed**
- [x] 真实栈：`s14_notification_webhook.py` **5/5**（含真实投递到 httpbin，2.1s 成功）
- [x] 迁移：`0017 → 0018` 在真实库执行成功
- [x] 任务注册：worker `inspect registered` 能看到 `deliver_notification`

## Notes

1. **最初打算硬禁私网、不留开关**——写验收时发现「内网告警网关」是这个系统的
   主场景之一（README 定位：公司内部系统），一律禁止等于功能对主场景不可用。
   于是加部署级开关 `NOTIFICATION_ALLOW_PRIVATE_HOSTS`（默认 false），
   但**元数据地址任何情况下都拦**（没有业务用途，只有攻击价值）。
2. **最初想用 `except Exception` 包 `self.retry`**——那会把 Celery 的正常
   Retry 异常吞掉，重试机制直接失效。改为只捕获 `MaxRetriesExceededError`。
3. **测试里 fake DNS 最初「一律返回公网」**——结果 127.0.0.1 也被放行，
   「私网被拒」那条测试变成永远假绿。改为「域名当公网、字面 IP 走真实判定」。
