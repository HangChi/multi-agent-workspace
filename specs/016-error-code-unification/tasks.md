# Tasks: 016-error-code-unification

- [X] T001 规格六件套（spec / plan / research / data-model / contracts / checklists）
- [X] T002 新增 `tests/integration/test_error_code_unification.py`：6 类资源「陌生人 → 404」+ 响应体同构（9 例）
- [X] T003 `request_service._check_owner` 改 `NOT_FOUND`「需求不存在」（FR-001）
- [X] T004 `agent_service._check_owner` 两处改 `NOT_FOUND`（FR-002）
- [X] T005 `group_service._check_owner` 与「加入他人 Agent」改 `NOT_FOUND`（FR-003/006）
- [X] T006 `egress_service` Agent 归属校验改 `NOT_FOUND`（FR-004）
- [X] T007 `external_grant_service` 三处归属校验改 `NOT_FOUND`（FR-005）
- [X] T008 `deps.get_owned_resource` 统一为 404 同构响应（保留工具函数，改语义并补「响应体相等」断言）
- [X] T009 更新既有测试里的 403 断言（17 个用例，含 6 个文件头注释）
- [X] T010 同步 `specs/001|005/contracts/api.md` 的错误码（FR-009）
- [X] T011 README「已知限制」删除「两套并存」，改为统一口径（SC-004）
- [X] T012 后端全量 + 覆盖率门禁：**2004 passed / 95.00%**（NFR-002）
- [X] T013 前端回归：tsc / eslint / vitest(143) / build 全绿（NFR-003）
- [X] T014 quickstart.md + docs/README 索引 + 合回 master
