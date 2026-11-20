# Implementation Plan: 016 错误码语义统一

**Branch**: `feat/016-error-code-unification` | **Spec**: [spec.md](./spec.md) | **Contracts**: [contracts/api.md](./contracts/api.md)

## Summary

把 8 处「非相关人访问」的 `FORBIDDEN` 改为 `NOT_FOUND`，message 改为中性的
存在性描述；保持 6 类「参与者权限不足」的 403 不动。无迁移、无前端逻辑改动。

## Technical Context

- **语言/框架**：Python 3.12+ / FastAPI / SQLAlchemy async
- **改动层次**：service 层（`AppError("FORBIDDEN", ...)` → `AppError("NOT_FOUND", ...)`）
- **不改**：路由层、schema、事务边界、审计、前端调用
- **测试**：`pytest`，覆盖率门禁 85%（`backend/.coveragerc`）
- **性能**：无影响（状态码不改变查询路径）

## Constitution Check

| 条款 | 影响 | 结论 |
|---|---|---|
| SDD：先规格后实现 | 本 plan 与 spec/contracts 先行 | ✅ |
| TDD：先写失败用例 | 先加「陌生人 → 404」与「响应体相等」用例，跑红，再改实现 | ✅ |
| 安全边界 | 减少信息泄露，不扩大任何授权 | ✅ |
| 不动数据模型 | 无迁移 | ✅ |
| 质量门禁 | ruff / mypy / pytest / 覆盖率全过 | ✅ |

## 改动清单（按文件）

| 文件 | 改动 | 覆盖 FR |
|---|---|---|
| `app/services/request_service.py` | `_check_owner` 改抛 `NOT_FOUND`「需求不存在」 | FR-001, FR-008 |
| `app/services/agent_service.py` | `_check_owner` ×2 改 `NOT_FOUND` | FR-002 |
| `app/services/group_service.py` | `_check_owner` 改 `NOT_FOUND`；加成员时他人 Agent 改 `NOT_FOUND` | FR-003, FR-006 |
| `app/services/egress_service.py` | Agent 归属校验改 `NOT_FOUND` | FR-004 |
| `app/services/external_grant_service.py` | 3 处归属校验改 `NOT_FOUND` | FR-005 |
| `app/api/deps.py` | 删除零调用的 `get_owned_resource`（其 403 语义已无调用方） | 一致性 |
| `tests/` | 更新既有 403 断言 → 404；新增「同构响应」用例 | FR-001..008 |
| `specs/001/003/005/contracts` | 同步错误码 | FR-009 |
| `README.md` | 删除「两套并存」段落，改为统一口径 | FR-009 |

## 实施顺序

1. **先补用例**（TDD 红）：`tests/integration/test_error_code_unification.py`
   覆盖 6 类资源的「陌生人 → 404」+「响应体相等」。
2. 逐 service 改实现（每次改完跑对应测试文件，避免一次改 8 处再猜是哪处错）。
3. 全量跑，清理既有 403 断言。
4. 同步 contracts 与 README。
5. 前端不动，但跑一遍 vitest + build 确认无回归。

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| 误改白名单里的 403 | contracts §4 列了 6 个精确位置，改完逐一 `grep` 复核 |
| 测试断言散落 10+ 文件，漏改 | 先 `grep -rn '403' tests` 出清单，按文件逐个过；全量跑兜底 |
| message 泄露存在性 | 新增「同构响应」用例（SC-003），message 一泄露就红 |
| 前端把 404 当"页面不存在" | 前端错误由 ErrorBanner 直通展示，无基于 403 的跳转（已核查） |

## 验证方式

- `pytest -q --cov=app`（两版本：3.12 本地 + 3.15 容器）
- `ruff check app tests` / `ruff format --check` / `mypy app`
- 前端 `npx tsc --noEmit && npx vitest run && npx vite build`
- 手工：用两个账号验证「他人需求 → 404」且响应体与随机 UUID 一致
