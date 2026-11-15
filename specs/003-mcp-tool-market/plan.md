# Implementation Plan: 外部工具服务器与工具市场

**Branch**: `feat/003-external-tools` | **Date**: 2026-11-15 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/003-mcp-tool-market/spec.md`

## Summary

为工作台增加一个受治理的外部 MCP 工具扩展闭环：管理员登记并验证外部服务器，审核者发布版本化工具，成员安装并授权给 Agent，Worker 在既有 ReAct 运行链路中按不可变版本快照调用。v1 只支持 Streamable HTTP；外部定义与 002 内置 `CATALOG` 分离，通过 `ToolExecutor` 适配器注入现有 Runner。所有服务器连接经过独立的 SSRF/出网策略连接器，凭证以加密形式保存，结果按不可信数据处理，暂停/撤销/漂移对新调用 fail-closed，调用、版本和治理变化进入统一审计及 WebSocket/运行详情。

本计划不实现或修复 002 未通过的 native Linux/full-stack acceptance；只消费现有 002 seams，并在实现前置依赖中明确其状态。

## Technical Context

**Language/Version**: Python 3.12+ backend; TypeScript/React 18 frontend

**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy 2 async, Alembic, Celery 5, Redis, existing `httpx`, LiteLLM, React/Vite/Tailwind/TanStack Query/Zustand

**Storage**: PostgreSQL 16 for server/tool/version/market/install/grant/credential/validation/invocation records; Redis/Celery for bounded background validation where needed; existing object storage is not needed for v1 tool definitions

**Testing**: pytest + async integration tests + unit tests; frontend Vitest/lint/typecheck/build; contract and native MCP fixture server tests

**Target Platform**: Linux Docker Compose deployment with FastAPI API processes and Celery Worker processes; external MCP servers accessed over HTTPS

**Project Type**: Full-stack web service with React SPA and asynchronous execution worker

**Performance Goals**:

- Preserve existing assignment API target of <100ms by keeping discovery/validation and external calls out of request handlers.
- 95% of successful external calls surface a success/failure state within 2 seconds after the remote response.
- Connect, initialize/discovery, and call deadlines are bounded by configuration and remaining Run budget; 20 concurrent success plus 20 concurrent failure/timeout calls do not fail core APIs.
- Market listing/search returns paginated results with the same bounded query behavior as existing list endpoints.

**Constraints**:

- No stdio, HTTP+SSE, arbitrary subprocess, custom image, or public third-party app store in v1.
- No credential plaintext in database responses, logs, model messages, steps, events, audit detail, or UI.
- HTTPS and endpoint validation; reject private/loopback/link-local/metadata/reserved destinations, unsafe redirects, query credentials and unapproved ports. Revalidate DNS/redirect targets at connection time.
- External outputs/descriptions are untrusted and bounded; no remote content may mutate grants, credentials, system instructions, audit or admin state.
- New calls require current published/installed/granted/credential state in addition to immutable Run snapshot; terminal Runs reject late results.
- API handlers perform validation/enqueueing only; Worker owns discovery/calls and uses existing Runner/state machine/quota/audit/WS paths.
- 002 native Linux/full-stack acceptance remains a separate dependency and is not silently claimed by 003.

**Scale/Scope**: One workbench deployment, existing admin/member/viewer roles, user-scoped installations because no workspace entity exists yet; paginated marketplace and audit queries; at least 20 concurrent external invocations in acceptance. The design leaves a migration point to workspace-scoped ownership later.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Gate | Result | Evidence / plan response |
|---|---|---|
| SDD + TDD | PASS | Existing 003 spec precedes this plan; tasks will require red/green unit, integration and frontend tests before implementation completion. |
| WHAT/WHY separation | PASS | `spec.md` remains product-facing; transport, models and connector choices are recorded here/research/design artifacts. |
| Complete production system | PASS | Lifecycle, credentials, SSRF, audit, version snapshots, failure handling and observability are included rather than deferred. |
| Existing stack | PASS | Uses Python/FastAPI/SQLAlchemy/Alembic/Celery/Redis, React/TypeScript/Vite and existing Runner; no LangGraph or second execution loop. |
| API/Worker split | PASS | Admin/member API endpoints validate and enqueue; Worker performs external discovery and calls. |
| Security boundaries | PASS | Encrypted credentials, RBAC/resource ownership, fail-closed grants, SSRF controls, redaction and audit are first-class design work. |
| Existing 002 boundary | PASS with dependency note | External definitions remain separate from static catalog; 002 unfinished native acceptance is reported as a prerequisite, not reimplemented. |

No gate violation requires Complexity Tracking justification.

## Research Decisions

See [research.md](research.md) for alternatives and rationale.

1. Streamable HTTP only; no stdio or legacy HTTP+SSE in v1.
2. Dedicated bounded `httpx` MCP adapter behind `ToolExecutor`.
3. External catalog/version data separate from builtin `CATALOG`.
4. Run snapshots pin identity/version/schema/permissions; current state is checked fail-closed at dispatch.
5. v1 uses encrypted server-scoped static Bearer/API-key credentials; OAuth is deferred.
6. SSRF and DNS/redirect validation are applied at registration and connection time.
7. Remote output is bounded untrusted data; external failures have explicit categories and reuse existing run timeline.

## Project Structure

### Documentation (this feature)

```text
specs/003-mcp-tool-market/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── api.md
├── checklists/
│   └── requirements.md
└── tasks.md                 # generated by /speckit-tasks
```

### Source Code (repository root)

```text
backend/
├── alembic/versions/0005_external_tools.py
├── app/models/
│   ├── external_tool.py       # server, versions, listings, installs, grants
│   ├── external_credential.py
│   ├── external_validation.py
│   └── external_invocation.py
├── app/schemas/
│   ├── external_tool.py       # server/market/install/grant DTOs
│   └── external_invocation.py
├── app/services/
│   ├── external_tool_service.py
│   ├── external_validation_service.py
│   ├── external_grant_service.py
│   ├── external_credential_service.py
│   └── external_invocation_service.py
├── app/api/v1/
│   ├── external_tool_servers.py
│   ├── marketplace.py
│   └── external_tool_grants.py
├── app/executors/
│   ├── mcp_client.py           # initialize/list/call + bounded transport
│   ├── mcp_security.py         # endpoint/SSRF/redirect/result policies
│   └── external_tool.py        # ToolExecutor adapter and error mapping
├── app/tasks/
│   ├── validate_external_server.py
│   └── execute_run.py           # inject snapshot-backed external executors
├── app/orchestrator/runner.py   # external failure/status/cancellation hooks
├── app/core/
│   ├── secrets.py               # encrypted credential boundary
│   ├── config.py                # safe MCP bounds/config
│   └── audit.py                 # strict audit use for mandatory facts
└── tests/
    ├── unit/test_mcp_client.py
    ├── unit/test_external_tool_security.py
    ├── unit/test_external_tool_adapter.py
    └── integration/
        ├── test_external_tool_servers.py
        ├── test_marketplace.py
        ├── test_external_tool_grants.py
        └── test_external_tool_invocations.py

frontend/src/
├── api/endpoints.ts             # DTOs and endpoint wrappers
├── pages/MarketplacePage.tsx
├── components/marketplace/
│   ├── MarketplaceFilters.tsx
│   ├── ToolListingCard.tsx
│   └── InstallToolDialog.tsx
├── components/admin/ExternalToolServers.tsx
├── components/agent/ExternalToolGrants.tsx
├── pages/AdminPage.tsx           # governance surface or linked section
├── components/layout/AppLayout.tsx
├── router.tsx
└── tests/
    ├── marketplace.test.tsx
    └── external-tool-grants.test.tsx

deploy/verify/
└── mcp_fixture_server.py         # deterministic test-only Streamable HTTP server
```

**Structure Decision**: Extend the existing web application in its current backend/frontend layout. Keep the transport/security adapter in `backend/app/executors`, lifecycle and authorization in services/models, HTTP contracts in API routers, and all calls in the existing Worker/Runner path. The frontend adds a member marketplace and admin governance surface, while Agent and Run pages consume the same catalog/grant/timeline concepts. A deterministic fixture server is test infrastructure only and is never started from production Worker code.

## Implementation Phases

### Phase 0 — Contract and security foundation

- Add configuration for MCP handshake/call deadlines, response byte/token caps, concurrency, retry policy (default no retry for unknown side effects), allowed ports and endpoint policy.
- Implement encrypted credential service with key version metadata, rotation/revocation and redaction tests. Do not reuse JWT refresh-token storage as a credential vault.
- Implement endpoint normalization, HTTPS/SSRF/DNS/redirect checks and safe error mapping.
- Define MCP JSON-RPC envelope validation and namespaced stable tool names; support initialization, paginated `tools/list`, and `tools/call` only.
- Add unit tests first for private/link-local/metadata rejection, DNS rebinding/redirect validation, bounded payloads, secret canary absence, malformed JSON-RPC, pagination, duplicate tools and unsupported transport.

### Phase 1 — Persistence, discovery and governance

- Add migration/models from [data-model.md](data-model.md), including immutable tool versions, listings, validation history, installation/grant scopes, credential metadata and invocation facts.
- Register models in `app/models/__init__.py`; add indexes/constraints and downgrade notes.
- Add schemas/services/routers for server registration, validation, listing, review, pause/revoke, credential rotation/revocation, marketplace browsing and installation.
- Keep validation work out of request handlers; use bounded Celery task or test-mode service path and make each transition/audit atomic.
- Add API contract tests for role/resource matrix, state transitions, duplicate registration, failed/partial discovery, no secret serialization and strict audit behavior.

### Phase 2 — Agent grants and runtime adapter

- Add installation and Agent grant resolution separate from `agents.tools`; verify owner/scope, listing state, version pin, permission subset and credential scope.
- Extend run assignment/execution setup to capture `RunExternalToolSnapshot` without mutating existing 002 name snapshots. Preserve immutable historical snapshots.
- Build authorized external executors for a Run and inject them into existing `ReActRunner`; do not add a second tool loop or API-side execution.
- Add local argument/schema validation, current-state fail-closed checks, bounded result sanitization, explicit error categories, no automatic non-idempotent retries, and invocation persistence.
- Extend Runner/step persistence and `tasks/events.py` with external invocation start/update/policy-denied projections. Ensure terminal-run/late-result checks and cancellation propagation.
- Add unit and integration tests for success, parameter rejection before network, tool error, transport/auth/timeout/policy errors, cancellation, late results, concurrency and version drift.

### Phase 3 — Frontend and operator experience

- Add typed endpoint/query/mutation wrappers and a member Marketplace page with search/filter, safe listing details and install action.
- Add admin server/validation/review/credential metadata surface with reason-confirmed governance actions; never render credential values.
- Add Agent external grant controls showing version, permissions, credential label/status and availability reason; preserve existing builtin tool behavior.
- Add Run detail cards and socket event handling for external invocation status using the existing timeline, not a parallel view.
- Add frontend tests for role visibility, install/grant flows, paused/revoked states, secret absence and real-time invocation updates.

### Phase 4 — End-to-end hardening and release gate

- Run the fixture-server quickstart, migration upgrade/downgrade checks, backend/Frontend quality gates and the 20+20 concurrency scenario.
- Run security regression matrix: SSRF targets, redirects, DNS changes, malformed schemas, prompt-injection-like outputs, credential canary, oversized results, repeated denial, cancellation and stale version.
- Verify audit coverage and immutable historical views; explicitly report any 002 native Linux/full-stack acceptance blocker separately.
- Update tasks/checklists and deployment documentation only after tests pass; no claim of 003 completion while a required gate fails.

## Dependencies and Risks

| Dependency/Risk | Mitigation / gate |
|---|---|
| 002 native/full-stack acceptance is currently failed | Treat as explicit upstream status; verify only interfaces used by 003 and do not fold its fixes into 003. |
| No existing encrypted credential store | Implement and test a narrowly scoped key-versioned service before any server endpoint can use credentials. |
| Worker HTTP does not automatically inherit sandbox egress firewall | Dedicated MCP connector validates destination at registration and connection; deployment network policy remains a defense-in-depth requirement. |
| Existing Runner marks generic executor exceptions as completed | Add an explicit external result/status mapping and tests before enabling external executors. |
| Existing `Agent.tools`/run snapshot store names only | Keep external grants and `RunExternalToolSnapshot` parallel and immutable; do not weaken 002 trigger/history behavior. |
| Remote tool output can contain prompt injection or huge payloads | Treat all remote data as untrusted, bound and sanitize before model/UI/audit projection; enforce authorization outside model text. |
| Server schema/list drift | Full paginated discovery, digest/versioning, review gate, optional list-change signal and fail-closed call-time contract check. |
| External server failure or non-idempotent side effects | Explicit error categories, bounded deadlines, no default retry, cancellation/late-result checks, per-server/user/run concurrency limits. |

## Complexity Tracking

No Constitution violations. The additional external catalog, immutable snapshots and connector are required because folding remote tools into the existing static catalog or name-only grants would break version governance, credential scope, SSRF control and historical reproducibility. A second execution loop, public app-store subsystem and stdio process manager were explicitly rejected as unnecessary scope and higher-risk alternatives.

## Post-Design Constitution Check

| Gate | Result | Evidence |
|---|---|---|
| Spec precedes implementation | PASS | `spec.md` and checklist exist before source changes. |
| All FRs have implementation/test anchors | PASS | API contract, data model, runtime phases and quickstart map discovery, governance, grants, invocation, security and audit FRs. |
| TDD and quality gates | PASS | Each phase starts with tests and release gate includes lint/type/test plus concurrency/security matrices. |
| Security and production readiness | PASS | Encryption, RBAC, SSRF, redaction, audit, lifecycle, snapshots and failure handling are explicit. |
| Existing architecture preserved | PASS | Existing Runner, state machine, quota, audit and WS are extended; no parallel runtime introduced. |
| Scope bounded | PASS | Streamable HTTP/static credentials/user scope only; 002 unfinished work and public marketplace features excluded. |

The plan is ready for `/speckit-tasks`.
