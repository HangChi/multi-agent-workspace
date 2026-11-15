# Data Model: 外部工具服务器与工具市场

**Feature**: 003-mcp-tool-market  
**Storage**: PostgreSQL 16 via async SQLAlchemy/Alembic

## Design rules

- 内置 `tools` / `CATALOG` 继续只描述内置工具；外部能力使用本文件实体。
- 外部工具定义和授权均不可依赖可变的名称 alone；使用 server identity + discovered version + tool identity。
- 运行、调用和审计历史保存不可变快照；状态变化只影响新调用。
- 所有外部服务器地址由服务端校验，凭证值只在 Worker 调用边界短暂解密。

## Entities

### ExternalToolServer

登记一个外部 MCP Streamable HTTP 服务器。

| Field | Type | Rules |
|---|---|---|
| id | UUID | PK |
| owner_id | UUID | FK users.id; admin/authorized maintainer scope |
| name | varchar | required; unique within owner scope |
| description | text | required; safe display text |
| endpoint_url | text | HTTPS only; no query credentials; SSRF validation |
| source_url | text nullable | maintenance/source reference, safe display only |
| maintainer | varchar | required |
| auth_mode | enum | `none`, `static_bearer`, `static_api_key` |
| status | enum | `draft`, `validation_failed`, `pending_review`, `published`, `paused`, `rejected`, `revoked` |
| review_reason | text nullable | required for reject/pause/revoke transitions |
| created_by | UUID | FK users.id |
| created_at / updated_at | timestamptz | server timestamps |
| last_validated_at | timestamptz nullable | latest validation completion |
| current_validation_status | enum nullable | `passed`, `failed`, `partial` |

Constraints: endpoint must not contain credentials; unique `(owner_id, normalized_endpoint_url)`; status changes audited.

### ServerCredential

服务器级静态认证材料的元数据和密文。

| Field | Type | Rules |
|---|---|---|
| id | UUID | PK |
| server_id | UUID | FK server; historical rows retained |
| scope_owner_id | UUID | FK users.id; authorization boundary |
| label | varchar | safe display label |
| kind | enum | `bearer`, `api_key` |
| header_name | varchar | allowlisted header name; never arbitrary hop-by-hop header |
| ciphertext | bytea/text | encrypted value; never serialized |
| key_version | varchar | encryption key version for rotation |
| status | enum | `active`, `revoked`, `superseded` |
| created_by / rotated_at / last_used_at | UUID/timestamptz | audit metadata |

Only metadata is returned by APIs. New calls may use only one active credential permitted by current owner scope.

### ExternalToolVersion

一次 `tools/list` 发现的工具定义快照。

| Field | Type | Rules |
|---|---|---|
| id | UUID | PK |
| server_id | UUID | FK server |
| external_name | varchar | name as declared by server |
| stable_name | varchar | generated namespaced runtime name, collision-free |
| version | varchar | server-declared or discovery fingerprint |
| description | text | sanitized and bounded |
| input_schema | JSONB | validated JSON Schema subset |
| output_schema | JSONB nullable | validated optional schema subset |
| capability_category | varchar nullable | safe market facet |
| required_permissions | JSONB | declared permissions, normalized |
| definition_hash | varchar | change detection |
| discovered_at | timestamptz | immutable |
| availability | enum | `valid`, `invalid`, `stale`, `revoked` |

Unique `(server_id, external_name, version)`; a changed schema creates a new version, never mutates an old one.

### MarketplaceListing

经审核后向成员展示的发布记录。

| Field | Type | Rules |
|---|---|---|
| id | UUID | PK |
| tool_version_id | UUID | FK ExternalToolVersion |
| status | enum | `pending_review`, `published`, `paused`, `rejected`, `revoked` |
| category / usage_summary | varchar/text | safe searchable/display metadata |
| data_handling | text | required before publication |
| restrictions | JSONB | limits and required permissions |
| reviewer_id | UUID nullable | FK users.id |
| reviewed_at / review_reason | timestamptz/text | reason required on negative transitions |
| published_at | timestamptz nullable | release timestamp |

At most one current listing per tool version; historical decisions retained.

### ToolInstallation

用户/工作台范围安装已发布工具的关系。由于现有代码没有 workspace 实体，v1 明确使用 owner/user scope，后续可迁移至 workspace。

| Field | Type | Rules |
|---|---|---|
| id | UUID | PK |
| listing_id | UUID | FK listing |
| owner_id | UUID | FK users.id |
| status | enum | `installed`, `disabled`, `removed` |
| version_policy | enum | `pinned`, `allow_patch` |
| installed_by | UUID | FK users.id |
| installed_at / updated_at | timestamptz | timestamps |

Unique `(owner_id, listing_id)` for active installation.

### ExternalToolGrant

Agent 对具体安装版本的授权。

| Field | Type | Rules |
|---|---|---|
| id | UUID | PK |
| agent_id | UUID | FK agents.id |
| installation_id | UUID | FK installation |
| tool_version_id | UUID | FK version |
| credential_id | UUID nullable | FK credential; only permitted server scope |
| permission_snapshot | JSONB | immutable granted permission subset |
| schema_snapshot | JSONB | immutable input/output schema used by run |
| status | enum | `active`, `disabled`, `revoked` |
| granted_by / granted_at | UUID/timestamptz | audit metadata |

Unique `(agent_id, tool_version_id)` while active. Grant writes require Agent ownership/admin authorization.

### ServerValidation

一次服务器验证的阶段结果。

| Field | Type | Rules |
|---|---|---|
| id | UUID | PK |
| server_id | UUID | FK server |
| requested_by | UUID | FK user |
| status | enum | `passed`, `failed`, `partial` |
| connection_status / auth_status / discovery_status | enum | per-stage result |
| discovered_count / valid_count | integer | non-negative |
| definition_digest | varchar nullable | complete discovery digest |
| safe_error | text nullable | sanitized, no secret/internal address detail |
| started_at / completed_at | timestamptz | timing |

### ExternalToolInvocation

一次运行期外部调用事实；与通用 Step 关联但保留外部特有分类。

| Field | Type | Rules |
|---|---|---|
| id | UUID | PK |
| run_id / step_id | UUID | FK run/step; historical rows retained |
| agent_id / owner_id | UUID | FK agent/user |
| server_id / tool_version_id | UUID | FK server/version |
| grant_id / credential_id | UUID nullable | references, no secret value |
| call_id | varchar | request correlation |
| status | enum | `succeeded`, `tool_error`, `parameter_error`, `transport_error`, `auth_error`, `timeout`, `policy_denied`, `cancelled`, `result_truncated` |
| duration_ms | integer | non-negative |
| input_summary | JSONB nullable | bounded redacted summary, not raw secret-bearing args |
| output_summary | JSONB nullable | bounded sanitized projection |
| policy_result | JSONB | preflight decisions |
| error_summary | text nullable | safe user/model-facing summary |
| created_at / completed_at | timestamptz | timestamps |

Indexes `(run_id, created_at)`, `(server_id, created_at)`, `(tool_version_id, created_at)`, `(owner_id, created_at)`.

### RunExternalToolSnapshot

不可变运行快照，避免依赖 002 当前仅有的名称数组。

| Field | Type | Rules |
|---|---|---|
| id | UUID | PK |
| run_id | UUID | FK run; one per authorized external grant |
| grant_id | UUID | FK grant |
| server_id / tool_version_id | UUID | identity snapshot |
| stable_name / external_name | varchar | runtime and display names |
| version | varchar | pinned version |
| permission_snapshot / schema_snapshot | JSONB | immutable |
| credential_id | UUID nullable | reference only |
| captured_at | timestamptz | immutable |

Unique `(run_id, stable_name)`. Historical run detail reads this table rather than current marketplace state.

## State transitions

### Server/listing lifecycle

`draft → pending_review → published → paused → published` (resume)  
`pending_review → rejected`  
`published/paused → revoked` (terminal for that version)

Only authorized governance roles may perform transitions; negative transitions require reason and audit.

### Installation/grant lifecycle

`installed → disabled → installed` or `installed → removed`  
`active grant → disabled/revoked`; revocation blocks new calls but does not rewrite history.

### Validation

`requested → running → passed|partial|failed`; a failed or partial validation cannot produce a published listing.
