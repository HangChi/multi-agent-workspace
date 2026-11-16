# Feature 003 Release Status

**Status: Not ready for production release.** This report reflects verification completed on 2026-11-16. The 003 implementation phases have commits through US5; Phase 8 has remaining deployment and integration gates.

## Verified

- Backend: 807 unit/integration tests passed, plus 6 subtests (full regression after transaction batching). The subsequent finite upstream-timeout diagnostic change passed 115 focused network/domain tests. The two attachment presigned-URL tests initially timed out because the local environment routed loopback traffic through a proxy; rerunning with `NO_PROXY=localhost,127.0.0.1` passed both. This was test-environment routing, not a 003 defect.
- Focused external security/runtime matrix: 33 passed.
- External lifecycle, audit, validation drift, invocation and observability subset: 18 passed.
- External mixed concurrency acceptance against deterministic MCP fixture (20 successes, 10 tool errors, 10 timeouts): 1 passed; maximum observed fixture in-flight calls was bounded at 8.
- Frontend: lint has 0 errors and 2 existing Fast Refresh warnings; typecheck passes; 48 tests pass; production build passes.
- Backend Ruff check passes. `git diff --check` passes after the verified edits.
- Database migration round-trip passed on a disposable PostgreSQL database: `upgrade head → downgrade base → upgrade head`; final revision `0005_external_tools (head)`. The base downgrade was hardened to remove the cyclic user/invite-code foreign keys and PostgreSQL enum types.
- Feature 002 native Linux/full-stack acceptance remains an independent prerequisite and is not claimed as completed by 003.

## Remaining gates

- Run the 20-success + 20-failure/timeout scenario through actual Worker/HTTP/API deployment and verify core API latency/error behavior; current acceptance uses the MCP fixture transport and does not measure core API P95.
- Complete production deployment smoke tests and verify operational alert thresholds with the deployment's metrics/log collector.
- Keep Feature 002 acceptance evidence separate from 003 attribution. Native Linux run [43774141487](https://github.com/HangChi/multi-agent-workspace/actions/runs/43774141487) passed backend/frontend checks, public HTTPS/IP/IPv6 (12/12), native I/O (2/2), escape matrix (13/13), concurrency bounds and cleanup, but failed domain policy setup, HTTP DNS runtime and strict API P95 (ratio 4.2479; required ≤1.2). Fix-validation run [43775194806](https://github.com/HangChi/multi-agent-workspace/actions/runs/43775194806) still failed: P95 baseline/load improved to 5.45/12.01 ms but ratio 2.2056 remained above 1.2; domain setup and DNS runtime still failed. Two additional HTTP checks observed terminal state before ordinary steps had been persisted; the real-database regression and `a98b5bb` address that window. None of these results completes the gate.

## Verified follow-up changes

- `03ef7a9`: reload Docker network metadata after attaching DNS, covering lazy IPAM gateway allocation; 118 focused tests plus 6 subtests and Docker Desktop domain probe 18/18 pass. Native Linux confirmation is still required.
- `9f47c91`: scope verification `NullPool` to Workers, matching production API pooling. All 90 acceptance-control tests pass, including resolved Compose checks for all fault Worker profiles; latency improvement is not claimed before measurement.
- `b450a99`: run polling selects only summary fields rather than verbose Step JSON, sandbox code/output or storage keys, and counts artifacts once. Real-database SQL projection, ownership and verbose execution detail contracts pass; this is not a substitute for the P95 gate.
- `a98b5bb`: persist steps with progress before terminal state becomes visible. The native checks that previously observed empty history passed again in runs `43776766217` and `43777625638`; HTTP functional acceptance remains 13/14 because of DNS runtime timeout.
- `fcf3bb5`: the real TLS/socket MCP fixture test found and fixed an incomplete initialized-notification HTTP response (nonzero Content-Length with no body). Discovery, success and tool-error paths now pass with certificate verification; this still does not complete T063.
- `860c1c9`: preserve the shared DNS proxy's primary gateway across dynamic sandbox attachments. Owned Docker probes verified the route fix, but native run `43777625638` still reported DNS runtime timeout, so route preservation is not claimed as its complete cause/fix. That run's strict P95 ratio was 3.9199 and still failed.
- `7197be6`: native finite diagnostics identified the domain fixture's missing implicit gateway. Define both fixture gateways explicitly; all 18 local real-domain cases pass. Native confirmation is pending.
- `47c0883`: install filter rules with one bounded `iptables-restore --noflush` transaction per family, preserving unrelated chains, rule order and best-effort cleanup behavior. The regression's subprocess count drops from 45 to 3; real domain 18/18 and IP/IPv6 11/11 local probes pass. P95 improvement is not claimed before measurement.
- Native fix-validation run `43778692370` is in progress on `47c0883`; none of its pending gates is marked complete.
