---

description: "Implementation tasks for external MCP tools and governed marketplace"
---

# Tasks: 外部工具服务器与工具市场

**Input**: Design documents from `/specs/003-mcp-tool-market/`

**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/api.md`, `quickstart.md`

**Approach**: TDD. Every business task is preceded by failing unit/contract/integration coverage. Complete and commit each phase before starting the next. 002 native Linux/full-stack acceptance remains an external dependency and is not implemented here.

## Phase 1: Setup

**Purpose**: Establish feature-specific configuration, fixtures, and test seams without changing 002 behavior.

- [X] T001 [P] Add MCP bounds and endpoint-policy settings with safe development defaults in `backend/app/core/config.py` and `deploy/.env.example`.
- [X] T002 [P] Add deterministic Streamable HTTP MCP fixture server scenarios in `deploy/verify/mcp_fixture_server.py` for success, pagination, schema drift, tool error, timeout, malformed response, and oversized result.
- [X] T003 [P] Add feature test fixture helpers and authenticated role fixtures in `backend/tests/conftest.py` and `backend/tests/external_tool_fixtures.py`.
- [X] T004 [P] Add frontend API test mocks for marketplace, server governance, grants, and invocation events in `frontend/src/test/mcpHandlers.ts`.
- [X] T005 Document 003 environment and fixture startup in `specs/003-mcp-tool-market/quickstart.md` and `deploy/README.md`. 

## Phase 2: Foundational

**Purpose**: Blocking security, protocol, secret, and persistence foundations. No user-story implementation starts until this phase passes.

- [X] T006 [P] Write failing unit tests for encrypted secret round-trip, key-version metadata, rotation, revocation, and canary redaction in `backend/tests/unit/test_external_credentials.py`.
- [X] T007 [P] Write failing unit tests for HTTPS/SSRF/DNS/redirect/port validation and safe error projection in `backend/tests/unit/test_mcp_security.py`.
- [X] T008 [P] Write failing unit tests for MCP JSON-RPC envelopes, initialize negotiation, paginated `tools/list`, duplicate detection, and unsupported transports in `backend/tests/unit/test_mcp_protocol.py`.
- [X] T009 Implement key-versioned encrypted credential service with write-only DTO boundary in `backend/app/core/secrets.py` and `backend/app/services/external_credential_service.py`.
- [X] T010 Implement endpoint normalization, DNS resolution checks, private/link-local/metadata/reserved blocking, redirect rejection/revalidation, and safe exceptions in `backend/app/executors/mcp_security.py`.
- [X] T011 Implement bounded Streamable HTTP JSON-RPC client with initialize, paginated discovery, cancellation, response limits, and no default side-effect retry in `backend/app/executors/mcp_client.py`.
- [X] T012 Write and apply Alembic migration for external server, credential, tool-version, listing, installation, grant, validation, run-snapshot, and invocation tables in `backend/alembic/versions/0005_external_tools.py`.
- [X] T013 Add SQLAlchemy models, enums, indexes, immutable snapshot relationships, and ORM exports in `backend/app/models/external_tool.py`, `backend/app/models/external_credential.py`, `backend/app/models/external_validation.py`, `backend/app/models/external_invocation.py`, and `backend/app/models/__init__.py`.
- [X] T014 Add shared Pydantic enums, bounded safe summaries, pagination DTOs, and redacted error schemas in `backend/app/schemas/external_tool.py` and `backend/app/schemas/external_invocation.py`.
- [X] T015 [P] Add strict audit action constants and secret-safe detail helpers in `backend/app/core/audit.py` and `backend/app/services/external_audit_service.py`.
- [X] T016 [P] Add model/API contract tests proving no credential value appears in responses, logs, audit detail, or websocket payloads in `backend/tests/unit/test_external_redaction.py`.

**Checkpoint**: migration upgrades cleanly; foundational security/protocol/secret tests pass; no user story can bypass these services.

## Phase 3: User Story 1 — 登记并验证外部工具服务器 (P1) 🎯 MVP

**Goal**: Authorized administrators can register, validate, and inspect a safe external server and its discovered versioned tools.

**Independent Test**: Valid HTTPS server reaches `pending_review` with complete paginated inventory; unreachable, unsafe, unauthorized, malformed, or incomplete servers fail with safe reasons and never become installable.

### Tests first

- [X] T017 [P] [US1] Add API contract tests for server create/list/detail/update/validate, status codes, role checks, and write-only credentials in `backend/tests/integration/test_external_tool_servers.py`.
- [X] T018 [P] [US1] Add integration tests for failed connection/auth/partial discovery, duplicate endpoint, schema incompleteness, and validation audit records in `backend/tests/integration/test_external_validation.py`.

### Implementation

- [X] T019 [US1] Implement server and validation services with lifecycle transitions, full pagination, digest/version creation, safe failure categories, and strict audit in `backend/app/services/external_validation_service.py` and `backend/app/services/external_tool_service.py`.
- [X] T020 [US1] Implement validation worker task and bounded test-mode path in `backend/app/tasks/validate_external_server.py` and `backend/app/tasks/celery_app.py`.
- [X] T021 [US1] Implement admin/maintainer server endpoints and validation endpoint in `backend/app/api/v1/external_tool_servers.py`, register it in `backend/app/main.py`.
- [X] T022 [US1] Add server/validation response projections and safe error mapping in `backend/app/schemas/external_tool.py`.
- [X] T023 [US1] Add server registration and validation integration coverage for pagination completeness, duplicate tools, drift digest, and no secret leakage.

**Checkpoint**: US1 independent test passes; commit Phase 3 before proceeding.

## Phase 4: User Story 2 — 审核并浏览第三方工具市场 (P1)

**Goal**: Reviewers govern discovered versions; members can search only published, validated, safe listings.

**Independent Test**: Pending, published, rejected, paused, and revoked fixtures produce the correct role-filtered market results and transitions with reasons and audits.

### Tests first

- [X] T024 [P] [US2] Add contract tests for marketplace listing/detail/search filters and approve/reject/pause/revoke transitions in `backend/tests/integration/test_marketplace.py`.
- [X] T025 [P] [US2] Add authorization tests for admin/reviewer/member/viewer visibility and reason-required negative transitions in `backend/tests/integration/test_marketplace_permissions.py`.

### Implementation

- [X] T026 [US2] Implement listing/review service with validation gate, immutable version references, lifecycle transitions, reason validation, and strict audit in `backend/app/services/marketplace_service.py`.
- [X] T027 [US2] Implement published listing query with bounded keyword/category/capability/version filters in `backend/app/services/marketplace_query_service.py`.
- [X] T028 [US2] Implement marketplace browse/detail and governance endpoints in `backend/app/api/v1/marketplace.py` and register router in `backend/app/main.py`.
- [X] T029 [US2] Add frontend marketplace DTOs, query/mutation wrappers, and invalidation keys in `frontend/src/api/endpoints.ts`.
- [X] T030 [US2] Build member marketplace filters, listing cards, detail/install entry point, empty/error states in `frontend/src/pages/MarketplacePage.tsx` and `frontend/src/components/marketplace/`.
- [X] T031 [US2] Build admin server/listing review controls with confirmation reasons and safe status projections in `frontend/src/components/admin/ExternalToolServers.tsx` and `frontend/src/pages/AdminPage.tsx`.
- [X] T032 [US2] Wire marketplace route/navigation and role visibility in `frontend/src/router.tsx` and `frontend/src/components/layout/AppLayout.tsx`.
- [X] T033 [P] [US2] Add frontend tests for listing visibility, filters, governance actions, and secret absence in `frontend/src/pages/__tests__/MarketplacePage.test.tsx`.

**Checkpoint**: US2 independent market/review test passes; commit Phase 4.

## Phase 5: User Story 3 — 安装外部工具并授权给 Agent (P1)

**Goal**: Members install published versions and grant exact version/permission/credential scopes to owned Agents without weakening builtin grants.

**Independent Test**: Authorized Agent exposes only its installed active grant; no-grant, stale-version, paused, revoked, wrong-owner, and credential-scope cases fail before network access.

### Tests first

- [X] T034 [P] [US3] Add installation/grant contract tests for create/list/revoke, ownership, version pinning, permission subsets, and credential scope in `backend/tests/integration/test_external_tool_grants.py`.
- [X] T035 [P] [US3] Add regression tests proving 002 builtin `agents.tools` and immutable `Run.tool_grants_snapshot` behavior is unchanged in `backend/tests/integration/test_builtin_tool_regression.py`.

### Implementation

- [X] T036 [US3] Implement installation and grant resolution services with owner/RBAC checks, active-state intersection, version pinning, permission subset validation, and fail-closed availability reasons in `backend/app/services/external_grant_service.py`.
- [X] T037 [US3] Implement install and Agent external-grant endpoints in `backend/app/api/v1/marketplace.py` and `backend/app/api/v1/external_tool_grants.py`; register router.
- [X] T038 [US3] Add immutable external grant/run snapshot creation at assignment/execution boundary in `backend/app/services/assignment_service.py`, `backend/app/models/external_tool.py`, and `backend/app/tasks/execute_run.py`.
- [X] T039 [US3] Add external grant DTOs and safe availability/version/credential metadata projections in `backend/app/schemas/external_tool.py`.
- [X] T040 [US3] Add Agent external-grant query/mutation wrappers and version/permission controls in `frontend/src/api/endpoints.ts` and `frontend/src/components/agent/ExternalToolGrants.tsx`.
- [X] T041 [US3] Integrate external-grant panel into Agent editor while preserving builtin tool selection and empty-tool semantics in `frontend/src/components/agent/AgentForm.tsx` and `frontend/src/pages/AgentPage.tsx`.
- [X] T042 [P] [US3] Add frontend grant tests for version pinning, revoked states, role ownership, and no credential values in `frontend/src/components/agent/__tests__/ExternalToolGrants.test.tsx`.

**Checkpoint**: US3 independent grant test passes; commit Phase 5.

## Phase 6: User Story 4 — 在运行中调用外部工具 (P1)

**Goal**: Authorized external tools run through the existing ReAct/state/event path with bounded, classified, cancel-safe results.

**Independent Test**: Success continues reasoning; invalid args do not reach network; remote tool errors, transport/auth errors, timeouts, policy denial, oversized output, cancellation and late responses are distinct and safe.

### Tests first

- [X] T043 [P] [US4] Add external executor unit tests for schema/permission preflight, MCP success/tool error/protocol error, result bounds, timeout, cancellation, and no retry in `backend/tests/unit/test_external_tool_executor.py`.
- [X] T044 [P] [US4] Add Runner tests for external failure status, terminal-run late-result rejection, and tool-message redaction in `backend/tests/unit/test_runner_external_tools.py`.
- [X] T045 [P] [US4] Add invocation integration tests for success, all error categories, quota/concurrency, cancellation, and audit facts in `backend/tests/integration/test_external_tool_invocations.py`.

### Implementation

- [X] T046 [US4] Implement `ToolExecutor` external adapter, local schema validation, bounded result sanitizer, credential injection, status/error mapping, and cancellation in `backend/app/executors/external_tool.py`.
- [X] T047 [US4] Implement authorized external executor construction from immutable run snapshots and current-state fail-closed checks in `backend/app/tasks/execute_run.py` and `backend/app/services/external_invocation_service.py`.
- [X] T048 [US4] Extend `ReActRunner` to preserve external failure categories, avoid completed status for failed external calls, bound tool messages, and reject late results in `backend/app/orchestrator/runner.py`.
- [X] T049 [US4] Persist invocation facts and publish start/update/policy-denied events using existing run timeline in `backend/app/models/external_invocation.py`, `backend/app/tasks/events.py`, and `backend/app/tasks/execute_run.py`.
- [X] T050 [US4] Extend run response and WebSocket schemas/handlers with safe external invocation projections in `backend/app/schemas/run.py`, `backend/app/api/v1/runs.py`, and `backend/app/api/v1/ws.py`.
- [X] T051 [US4] Add Run detail external invocation card and reducer support without creating a second execution view in `frontend/src/components/run/ExternalToolInvocationCard.tsx`, `frontend/src/pages/RunDetailPage.tsx`, and `frontend/src/hooks/useRunSocket.ts`.
- [X] T052 [US4] Add end-to-end fixture-server quickstart test covering successful continuation and all required failure classifications in `backend/tests/integration/test_external_mcp_e2e.py`.

**Checkpoint**: US4 independent invocation test passes; commit Phase 6.

## Phase 7: User Story 5 — 管理版本、凭证与使用审计 (P2)

**Goal**: Operators can rotate/revoke credentials, control versions, pause integrations, and query complete secret-safe audit facts.

**Independent Test**: Credential rotation, version upgrade choice, pause/revoke, and filtered invocation audit prove new calls use current allowed state while history remains immutable and secret-free.

### Tests first

- [X] T053 [P] [US5] Add lifecycle integration tests for credential rotation/revocation, version upgrade, pause/resume/revoke, in-flight cancellation, and historical snapshots in `backend/tests/integration/test_external_lifecycle.py`.
- [X] T054 [P] [US5] Add audit query/filter and canary-secret absence tests in `backend/tests/integration/test_external_tool_audit.py`.

### Implementation

- [X] T055 [US5] Implement credential rotation/revocation and version upgrade services with immediate new-call effect and immutable history in `backend/app/services/external_credential_service.py` and `backend/app/services/marketplace_service.py`.
- [X] T056 [US5] Add filtered external invocation projections to admin/audit endpoints without exposing raw arguments/results/secrets in `backend/app/api/v1/admin.py`, `backend/app/services/external_invocation_service.py`, and `backend/app/schemas/external_invocation.py`.
- [X] T057 [US5] Add operator UI for credential metadata/rotation, version comparison, pause/resume/revoke and invocation filters in `frontend/src/components/admin/ExternalToolServers.tsx` and `frontend/src/pages/AdminPage.tsx`.
- [X] T058 [US5] Add release alerts/diagnostic counters for validation drift, repeated auth/policy failures, destination changes, result caps, and audit gaps in `backend/app/services/external_observability.py` and `backend/app/core/log_setup.py`.
- [X] T059 [US5] Add lifecycle and audit frontend tests for status transitions, redaction, filters, and historical version display in `frontend/src/tests/external-lifecycle.test.tsx`.

**Checkpoint**: US5 independent lifecycle/audit test passes; commit Phase 7.

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Verify the complete feature and record truthful release status.

- [X] T060 [P] Run backend migration upgrade/downgrade, lint, type checks, unit and integration suites; fix only 003 regressions in `backend/`.
- [X] T061 [P] Run frontend lint, typecheck, Vitest, and production build; fix only 003 regressions in `frontend/`.
- [X] T062 [P] Run SSRF, credential canary, prompt-injection-like output, malformed schema, oversized response, drift, cancellation, and repeated denial security matrix in `backend/tests/`.
- [ ] T063 Run 20-success + 20-failure/timeout concurrency acceptance and verify core API P95/error behavior in `deploy/verify/` and `backend/tests/integration/`.
- [X] T064 Update `docs/architecture.md`, `deploy/README.md`, `specs/003-mcp-tool-market/quickstart.md`, and `specs/003-mcp-tool-market/tasks.md` with verified behavior and known 002 acceptance blocker.
- [X] T065 Run `git diff --check`, `git status`, and final scope review; ensure no secrets, fixture credentials, generated artifacts, or 002 fixes are included.

## Dependencies & Execution Order

- Phase 1 has no feature dependency and can start immediately.
- Phase 2 depends on Phase 1 and blocks every user story.
- US1 depends on Phase 2 and is the MVP. US2 depends on US1's discovered-version data. US3 depends on US2's published listings and US1's server identity. US4 depends on US3's grants/snapshots. US5 depends on US4's invocation/audit facts.
- Phase 8 depends on all intended stories. Do not claim completion if a required phase gate fails.

## Parallel Opportunities

- Phase 1 tasks T001–T004 are parallelizable.
- Phase 2 tests T006–T008 and T015–T016 are parallelizable; implementation follows their red state.
- Within each story, tests marked `[P]` can run together before implementation; frontend and backend work can proceed in parallel after the shared contract is stable.
- Do not parallelize changes to `runner.py`, `execute_run.py`, migrations, or shared router registration with conflicting tasks.

## Implementation Strategy

1. Complete Phase 1 and Phase 2; commit each phase.
2. Implement and validate US1 as the MVP; commit before US2.
3. Add governance, installation/grants, runtime invocation, then lifecycle/audit in dependency order; commit each phase.
4. Run Phase 8 only after every story checkpoint passes.
5. Keep each commit scoped to its phase and use the required attribution line.
