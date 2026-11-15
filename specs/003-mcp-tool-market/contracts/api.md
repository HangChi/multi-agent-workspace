# Feature 003 API Contract: 外部工具服务器与工具市场

All endpoints use `/api/v1`, JSON, and the existing Bearer access token. Error envelopes follow existing `AppError` conventions. Secrets are write-only: request payloads may contain a credential value, responses and errors never do.

## Authorization matrix

| Area | admin | member | viewer |
|---|---:|---:|---:|
| Register/update/validate external server | own/managed scope | no | no |
| Review/publish/pause/revoke server/tool | yes or granted reviewer role | no | no |
| Browse published marketplace | yes | yes | read-only if resource visibility permits |
| Install published tool | yes/member-owned scope | own scope | no |
| Grant/revoke tool on owned Agent | yes/member-owned scope | own Agent | no |
| Rotate/revoke server credential | server owner/admin | only if explicitly delegated | no |
| Read invocation/audit details | own runs; admin all | own runs | own visible runs, no secret fields |

## Server administration

### `POST /external-tool-servers`

Register a server and create an initial validation request.

Request:

```json
{
  "name": "Team Search",
  "description": "Searches the approved internal knowledge source",
  "endpoint_url": "https://tools.example.test/mcp",
  "source_url": "https://git.example.test/team-search",
  "maintainer": "Platform Team",
  "auth_mode": "static_bearer",
  "credential": {
    "label": "team-search-prod",
    "kind": "bearer",
    "value": "write-only-secret"
  }
}
```

Response `201`:

```json
{
  "id": "uuid",
  "name": "Team Search",
  "endpoint_url": "https://tools.example.test/mcp",
  "auth_mode": "static_bearer",
  "status": "pending_review",
  "credential": {"id": "uuid", "label": "team-search-prod", "status": "active"},
  "last_validation": {"status": "passed", "discovered_count": 2}
}
```

Failure: `400` invalid/unsafe endpoint, `403` role, `409` duplicate normalized endpoint, `422` invalid fields. Credential value is absent from every response.

### `GET /external-tool-servers`

Admin/maintainer list with filters `status`, `q`, `page`, `page_size`. Returns safe metadata and latest validation summary, never credential values.

### `GET /external-tool-servers/{server_id}`

Returns server metadata, lifecycle status, current discovered versions and latest validation summary subject to ownership/admin authorization.

### `PATCH /external-tool-servers/{server_id}`

Updates safe metadata or endpoint. Endpoint changes force a new validation and return `status=pending_review`; active listings are not silently changed.

### `POST /external-tool-servers/{server_id}/validate`

Runs bounded connectivity/auth/discovery validation. Response `202` when queued or `200` when synchronous test mode returns a completed `ValidationOut`. Failure remains visible as a validation record and cannot publish.

### `POST /external-tool-servers/{server_id}/credentials/rotate`

Request:

```json
{"label": "team-search-rotated", "kind": "bearer", "value": "write-only-secret"}
```

Response `200` returns credential metadata only. New calls use the new active credential; the previous credential becomes `superseded`. Audit action: `external_credential_rotate`.

### `POST /external-tool-servers/{server_id}/credentials/{credential_id}/revoke`

Response `200` with `status=revoked`; new calls fail closed before network access. Audit action: `external_credential_revoke`.

## Governance and marketplace

### `GET /marketplace/tools`

Member-visible published listings only. Query: `q`, `category`, `capability`, `maintainer`, `version`, `page`, `page_size`. Response `200`:

```json
{
  "items": [
    {
      "listing_id": "uuid",
      "tool_version_id": "uuid",
      "stable_name": "mcp_team_search_search",
      "display_name": "Team Search / search",
      "description": "Search approved knowledge",
      "server_name": "Team Search",
      "version": "1.2.0",
      "permissions": ["knowledge.read"],
      "restrictions": {"timeout_seconds": 15, "max_result_bytes": 65536},
      "status": "published",
      "validated_at": "2026-11-15T12:00:00Z"
    }
  ],
  "total": 1
}
```

Paused/rejected/revoked listings are not installable. Historical run projections are not marketplace results.

### `GET /marketplace/tools/{listing_id}`

Returns the full safe listing, schema summary and review/validation metadata. No endpoint credential, raw server error, or secret value is exposed.

### `POST /external-tool-servers/{server_id}/tools/{tool_version_id}/approve`

Reviewer-only. Request `{"reason":"Reviewed source, permissions, and data handling"}`. Publishes a listing only if validation and required metadata pass. `200`; audit action `external_tool_approve`.

### `POST /external-tool-servers/{server_id}/tools/{tool_version_id}/reject`

Reviewer-only. Requires a non-empty reason. `200`; audit action `external_tool_reject`.

### `POST /marketplace/tools/{listing_id}/pause` and `/revoke`

Reviewer/admin-only. Requires reason. Pause blocks new calls but may be resumed; revoke is terminal for the version. `200`; audit actions `external_tool_pause` / `external_tool_revoke`.

## Installation and Agent grants

### `GET /marketplace/installations`

Returns installations in the current user's scope, including installed version policy and status.

### `POST /marketplace/tools/{listing_id}/install`

Request:

```json
{"version_policy":"pinned"}
```

Response `201` returns installation metadata and listing version. Only `published` listings can be installed. Audit action `external_tool_install`.

### `POST /agents/{agent_id}/external-tool-grants`

Request:

```json
{
  "installation_id": "uuid",
  "tool_version_id": "uuid",
  "credential_id": "uuid",
  "permissions": ["knowledge.read"]
}
```

Response `201` returns stable tool name, pinned version, safe permission snapshot and credential metadata. The service verifies Agent ownership, installation scope, listing publication, credential scope, and permission subset before saving. Audit action `external_tool_grant`.

### `GET /agents/{agent_id}/external-tool-grants`

Returns active and disabled grant projections with version, status, permission summary and availability reason. Never returns credential values.

### `DELETE /agents/{agent_id}/external-tool-grants/{grant_id}`

Revokes the grant for new calls, retains history, and writes `external_tool_grant_revoke`.

## Run and invocation projections

### `GET /runs/{run_id}`

The existing run response gains an `external_tool_invocations` projection containing bounded metadata:

```json
{
  "id": "uuid",
  "step_id": "uuid",
  "server_name": "Team Search",
  "tool_name": "search",
  "stable_name": "mcp_team_search_search",
  "version": "1.2.0",
  "status": "succeeded",
  "duration_ms": 184,
  "error_category": null,
  "result_truncated": false,
  "created_at": "2026-11-15T12:01:00Z"
}
```

No raw credential, full sensitive arguments, or unbounded result is returned.

### WebSocket `/ws/runs/{run_id}`

Existing authenticated run stream adds events on the same timeline:

- `external_tool_call_started`: `{step_seq, stable_name, version}`
- `external_tool_call_update`: `{step_seq, status, duration_ms, error_category, result_truncated}`
- `external_tool_policy_denied`: `{step_seq, stable_name, reason}`

Events contain no credentials and no raw server output. Late updates after a terminal run are discarded.

## Error categories

| Category | Meaning | Network request sent? |
|---|---|---:|
| `parameter_error` | Local schema/permission validation failed | no |
| `policy_denied` | Server/listing/grant/credential/egress state disallows call | no |
| `transport_error` | DNS/connect/protocol/availability failure | maybe |
| `auth_error` | Remote authentication rejected | yes |
| `timeout` | Connect, handshake or call deadline exceeded | maybe |
| `tool_error` | MCP result has `isError=true` | yes |
| `result_truncated` | Remote result exceeded safe output bounds | yes |
| `cancelled` | Run cancellation or terminal deadline won | maybe |

All errors use the existing JSON error envelope and safe, user-readable messages. Internal IPs, headers, stack traces and credential values are not returned.
