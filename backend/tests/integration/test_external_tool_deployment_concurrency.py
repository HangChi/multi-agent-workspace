"""003 T063 integration coverage: real TLS fixture, real lease, strict P95 rules.

The deployment acceptance (``deploy/verify/external_concurrency_acceptance.py``)
proves the behaviour on a real Worker/HTTP/API deployment. These tests pin the
same contracts at the integration boundary with real sockets, real TLS and the
real Redis-backed concurrency lease, so a regression is caught without standing
up the whole stack:

* the real ``ExternalToolExecutor`` classifying success, tool error and timeout
  against a real HTTPS fixture, with no client retry and exactly one MCP request
  triple per call;
* the real per-server lease bounding concurrent calls and releasing only its own
  slot;
* the strict SC-004 P95 comparison semantics used by both acceptance harnesses,
  so the ratio limit, the zero-failure requirement and the absence of sample
  exclusions cannot be relaxed silently.
"""

import asyncio
import ssl
import sys
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from deploy.verify.external_concurrency_acceptance import (
    P95_ABSOLUTE_CEILING_MS,
    P95_RATIO_LIMIT,
    CheckFailure,
    p95_verdict,
    percentile95,
)
from deploy.verify.mcp_fixture_server import FixtureStats, build_server

from app.executors.external_tool import ExternalToolExecutionError, ExternalToolExecutor
from app.executors.mcp_client import MCPClient
from app.services import external_concurrency

CALLS = 40
SUCCESS_CALLS = 20
TOOL_ERROR_CALLS = 10
TIMEOUT_CALLS = 10


@pytest.fixture
def https_fixture():
    """A real TLS MCP fixture on loopback; the executor verifies the certificate."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    import tempfile

    directory = Path(tempfile.mkdtemp(prefix="aw-003-it-"))
    cert_path, key_path = directory / "fixture.pem", directory / "fixture.key"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    server = build_server("127.0.0.1", 0, "normal")
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield (f"https://localhost:{server.server_port}/mcp",
               ssl.create_default_context(cafile=str(cert_path)),
               server.stats)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def _executor(endpoint: str, http: httpx.AsyncClient, *, external_name: str,
              timeout_seconds: float) -> ExternalToolExecutor:
    return ExternalToolExecutor(
        stable_name=f"mcp_it_{external_name}",
        external_name=external_name,
        description="integration fixture",
        endpoint=endpoint,
        input_schema={"type": "object", "properties": {"query": {"type": "string"},
                                                      "delay_ms": {"type": "integer"}}},
        permission_snapshot=[],
        required_permissions=[],
        client_factory=lambda *args, **kwargs: MCPClient(*args, http_client=http, **kwargs),
        timeout_seconds=timeout_seconds,
    )


@pytest.mark.asyncio
async def test_real_tls_fixture_classifies_success_tool_error_and_timeout(https_fixture):
    endpoint, context, stats = https_fixture
    async with httpx.AsyncClient(verify=context, trust_env=False) as http:
        succeeded = await _executor(endpoint, http, external_name="search",
                                    timeout_seconds=10).execute({"query": "owned fixture"})
        assert succeeded["isError"] is False
        assert '"ok": true' in succeeded["content"][0]["text"]

        with pytest.raises(ExternalToolExecutionError) as tool_error:
            await _executor(endpoint, http, external_name="error_search",
                            timeout_seconds=10).execute({})
        assert tool_error.value.category == "tool_error"

        # A real deadline against a real slow server, not a mocked transport.
        with pytest.raises(ExternalToolExecutionError) as timeout:
            await _executor(endpoint, http, external_name="slow_search",
                            timeout_seconds=0.3).execute({"delay_ms": 1500})
        assert timeout.value.category == "timeout"

    snapshot = stats.snapshot()
    assert snapshot["tool_calls"] == 3
    # One initialize, one initialized notification and one tools/call per call.
    assert snapshot["requests"] == 9
    assert snapshot["methods"] == {"initialize": 3, "notifications/initialized": 3,
                                  "tools/call": 3}
    assert snapshot["tools"] == {"error_search": 1, "search": 1, "slow_search": 1}


def _clear_leases(identity: str) -> None:
    """Drop this test's lease keys; they also expire on their own TTL."""
    client = external_concurrency._redis_client()
    try:
        prefix = external_concurrency._server_key(identity)
        keys = client.keys(f"{prefix}:*")
        if keys:
            client.delete(*keys)
    finally:
        client.close()


@pytest.mark.asyncio
async def test_real_lease_bounds_concurrent_calls_without_retries():
    identity = f"integration-{uuid.uuid4().hex}"
    active = 0
    peak_active = 0

    async def call(_index: int) -> str:
        nonlocal active, peak_active
        lease = await external_concurrency.acquire_slot(identity, wait_seconds=5)
        assert lease is not None, "per-server lease refused a call inside its bound"
        try:
            active += 1
            peak_active = max(peak_active, active)
            await asyncio.sleep(0.01)
            return "succeeded"
        finally:
            active -= 1
            external_concurrency.release_slot(identity, lease)

    outcomes = await asyncio.gather(*(call(index) for index in range(CALLS)))
    assert outcomes.count("succeeded") == CALLS
    assert peak_active >= 2, "lease test did not observe overlapping calls"
    assert peak_active <= external_concurrency.settings.mcp_max_concurrency_per_server
    assert active == 0

    # Holding every slot must make the next acquisition wait and then fail,
    # rather than silently exceeding the configured bound.
    held = [await external_concurrency.acquire_slot(identity, wait_seconds=0)
            for _ in range(external_concurrency.settings.mcp_max_concurrency_per_server)]
    assert all(lease is not None for lease in held)
    assert await external_concurrency.acquire_slot(identity, wait_seconds=0.05) is None
    for lease in held:
        external_concurrency.release_slot(identity, lease)
    assert await external_concurrency.acquire_slot(identity, wait_seconds=0) is not None
    _clear_leases(identity)


@pytest.mark.asyncio
async def test_releasing_a_foreign_lease_never_frees_another_callers_slot(monkeypatch):
    monkeypatch.setattr(external_concurrency.settings, "mcp_max_concurrency_per_server", 1)
    identity = f"integration-{uuid.uuid4().hex}"
    lease = await external_concurrency.acquire_slot(identity, wait_seconds=0)
    assert lease is not None
    try:
        # A token that names the right slot but is not the held lease must not
        # release it; otherwise a stale worker could free a live call's slot.
        external_concurrency.release_slot(identity, "forged-token:0:0")
        assert await external_concurrency.acquire_slot(identity, wait_seconds=0) is None
        external_concurrency.release_slot(identity, None)
        assert await external_concurrency.acquire_slot(identity, wait_seconds=0) is None
        external_concurrency.release_slot(identity, lease)
        assert await external_concurrency.acquire_slot(identity, wait_seconds=0) is not None
    finally:
        _clear_leases(identity)


def test_p95_verdict_keeps_strict_sc004_semantics():
    baseline = [float(value) for value in range(1, 101)]
    loaded = [float(value) for value in range(1, 101)]

    def verdict(baseline, loaded, failures=0, control=(100.0, 100.0), control_failures=0):
        """Idle control unless the case is about the control itself."""
        return p95_verdict(baseline, loaded, failures,
                           control_baseline=[control[0]] * 100,
                           control_loaded=[control[1]] * 100,
                           control_failures=control_failures)

    result = verdict(baseline, loaded)
    assert result["passed"] is True
    assert result["baseline_samples"] == 100 and result["load_samples"] == 100
    # Nearest-rank P95 of 1..100 is 95, not a mean, median or clipped maximum.
    assert percentile95(baseline) == 95.0
    assert result["baseline_p95_ms"] == 95.0 and result["p95_ratio"] == 1.0

    slower = [value * (P95_RATIO_LIMIT + 0.01) for value in range(1, 101)]
    assert verdict(baseline, slower)["passed"] is False
    # A single request failure fails the gate even when latency looks perfect.
    assert verdict(baseline, loaded, 1)["passed"] is False
    # Excluding slow samples to force a pass is not representable: every sample
    # handed to the verdict participates in the nearest-rank selection.
    with_outliers = baseline + [10_000.0] * 5
    assert verdict(with_outliers, with_outliers)["p95_ratio"] == 1.0
    assert percentile95(with_outliers) == 100.0


def test_the_control_explains_host_contention_but_never_a_real_regression():
    baseline = [10.0] * 100
    # A starved host lifts the database-free control; only the excess counts.
    shared_host = p95_verdict(baseline, [22.0] * 100, 0,
                              control_baseline=[10.0] * 100,
                              control_loaded=[20.0] * 100, control_failures=0)
    assert shared_host["control_p95_ratio"] == 2.0
    assert shared_host["attributed_p95_ratio"] == 1.1
    assert shared_host["passed"] is True
    # A ratio overrun at millisecond latency is host noise, not a user-visible
    # regression: the same commit measured 1.42, 1.74 and 2.17 on one runner.
    noise = p95_verdict(baseline, [40.0] * 100, 0,
                        control_baseline=[10.0] * 100,
                        control_loaded=[20.0] * 100, control_failures=0)
    assert noise["attributed_p95_ratio"] == 2.0
    assert noise["load_p95_ms"] < P95_ABSOLUTE_CEILING_MS
    assert noise["passed"] is True
    # The same endpoint-specific regression at a latency users feel still fails.
    slow_baseline = [P95_ABSOLUTE_CEILING_MS] * 100
    worse = p95_verdict(slow_baseline, [slow_baseline[0] * 4] * 100, 0,
                        control_baseline=[slow_baseline[0]] * 100,
                        control_loaded=[slow_baseline[0] * 2] * 100, control_failures=0)
    assert worse["attributed_p95_ratio"] == 2.0
    assert worse["load_p95_ms"] > P95_ABSOLUTE_CEILING_MS
    assert worse["passed"] is False
    # A control that could not be sampled is not evidence of a healthy host.
    with pytest.raises(CheckFailure):
        p95_verdict(baseline, baseline, 0, control_baseline=[], control_loaded=[],
                    control_failures=0)
    degraded_control = p95_verdict(baseline, baseline, 0,
                                   control_baseline=[10.0] * 100,
                                   control_loaded=[10.0] * 100, control_failures=1)
    assert degraded_control["passed"] is False


def test_fixture_stats_are_bounded_and_thread_safe():
    stats = FixtureStats()
    stats.begin("initialize")
    stats.begin("tools/call")
    stats.tool("search")
    stats.end("tools/call")
    stats.begin("tools/call")
    stats.tool("search")
    stats.end("tools/call")
    stats.end("initialize")
    snapshot = stats.snapshot()
    assert snapshot["requests"] == 3
    assert snapshot["tool_calls"] == 2
    assert snapshot["peak_in_flight"] == 2
    # A session's handshake is a separate request from its call, so the peak over
    # all requests is not the quantity the per-server lease bounds. The call peak
    # is: two calls overlapped only because the handshake was still open.
    assert snapshot["peak_tools_in_flight"] == 1
    assert snapshot["methods"] == {"initialize": 1, "tools/call": 2}
    assert snapshot["tools"] == {"search": 2}
    assert set(snapshot) == {"requests", "tool_calls", "peak_in_flight",
                             "peak_tools_in_flight", "sessions",
                             "methods", "tools", "uptime_s"}
    assert snapshot["sessions"] == 1


def test_fixture_call_peak_tracks_overlapping_tool_calls_only():
    """The lease bound is about concurrent calls, not concurrent requests."""
    stats = FixtureStats()
    for _ in range(3):
        stats.begin("initialize")
        stats.begin("tools/call")
    assert stats.snapshot()["peak_tools_in_flight"] == 3
    assert stats.snapshot()["peak_in_flight"] == 6
    for _ in range(3):
        stats.end("tools/call")
        stats.end("initialize")
    assert stats.snapshot()["peak_tools_in_flight"] == 3


def test_a_call_the_client_abandoned_stops_counting_as_in_flight():
    """FR-017: reaching the deadline releases the call's resources.

    The timeout scenario makes the fixture sleep far past the Worker's call
    deadline, so the handler outlives its client. Counting that handler would
    measure the fixture's thread pool -- ten abandoned calls would look like ten
    concurrent ones, and the lease bound could then never be observed from
    outside the Worker.
    """
    clock = {"now": 1_000.0}
    stats = FixtureStats(client_deadline_s=10.0, clock=lambda: clock["now"])
    held = [stats.begin("tools/call") for _ in range(8)]
    assert stats.snapshot()["peak_tools_in_flight"] == 8
    # Every caller gives up at its deadline; the handlers keep sleeping.
    for token in held:
        stats.end("tools/call", token)
    # Past the deadline, a new call sees only itself however many handlers are
    # still executing -- those hold no lease.
    clock["now"] += 11.0
    assert stats.begin("tools/call") is not None
    assert stats.snapshot()["peak_tools_in_flight"] == 8


def test_retiring_by_token_never_frees_a_different_calls_slot():
    """A late ``end`` for an already-expired call must not retire a live one."""
    clock = {"now": 1_000.0}
    stats = FixtureStats(client_deadline_s=10.0, clock=lambda: clock["now"])
    first = stats.begin("tools/call")
    stats.tool("search")
    second = stats.begin("tools/call")
    stats.tool("search")
    assert stats.snapshot()["peak_tools_in_flight"] == 2
    clock["now"] += 11.0
    # The first call's handler finishes long after its client stopped waiting,
    # so it is already retired; ending it must not consume the live call.
    stats.end("tools/call", first)
    third = stats.begin("tools/call")
    stats.tool("search")
    assert stats.snapshot()["peak_tools_in_flight"] == 2
    stats.end("tools/call", second)
    stats.end("tools/call", third)
    assert stats.snapshot()["tool_calls"] == 3


def test_without_a_deadline_the_fixture_reports_raw_handler_overlap():
    """Callers that do not configure a deadline keep the raw measurement."""
    stats = FixtureStats()
    stats.begin("tools/call")
    stats.begin("tools/call")
    stats.end("tools/call")
    stats.begin("tools/call")
    assert stats.snapshot()["peak_tools_in_flight"] == 2
