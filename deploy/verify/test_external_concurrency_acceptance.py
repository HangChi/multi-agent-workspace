"""Reverse controls for the 003 deployment acceptance harness itself.

``external_concurrency_acceptance.py`` decides whether T063 is complete, so the
decisions it makes are themselves under test here: the audit route it queries,
the record count it expects, the duration bounds it enforces, the retry policy
for transient registry failures and the strictness of the SC-004 P95 verdict.
Every one of these was a real defect found by a real-stack run:

* the audit route was ``/admin/audit-logs/...`` while the API serves
  ``/audit-logs/...``, so the invocation audit returned 404;
* the warm-up run produces one real invocation, so a count that excluded it was
  wrong by exactly one;
* a submission that raised was recorded with its exception class only, leaving
  "command failed" with no cause in the published artifact;
* ``build_with_retry`` caught ``AcceptanceFailure`` while ``require`` raises
  ``CheckFailure``, so a transient Docker Hub outage aborted the run;
* the fixture's request peak counted a session's handshake, not concurrent tool
  calls, so the lease bound was checked against the wrong quantity;
* the fixture TLS directory kept ``mkdtemp``'s 0700 mode, so the Worker -- which
  runs as uid 10001 -- could not read the CA bundle through a native Linux bind
  mount, died at import on ``SSL_CERT_FILE``, and was reported only as "worker
  did not become ready" with no evidence attached.

Run: python -m unittest discover -s deploy/verify -p 'test_*.py' -v
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import external_concurrency_acceptance as external
import httpx

try:  # Only the settings object is needed; no database or fixture.
    from app.core.config import settings
except ImportError:  # pragma: no cover - the backend package is installed in CI
    settings = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parents[2]


def _verifier(**attributes: object) -> external.ExternalVerifier:
    """A verifier whose HTTP client never opens a socket."""
    verifier = external.ExternalVerifier(
        SimpleNamespace(base="http://127.0.0.1:1/", timeout=60, poll_interval=0.5),
        httpx.AsyncClient())
    for name, value in attributes.items():
        setattr(verifier, name, value)
    return verifier


def _response(payload: dict) -> SimpleNamespace:
    return SimpleNamespace(json=lambda: payload)


def _invocation(run_id: str, status: str, duration_ms: int) -> dict:
    return {"run_id": run_id, "status": status, "completed_at": "2026-11-16T00:00:00Z",
            "duration_ms": duration_ms}


class AuditSurfaceControls(unittest.TestCase):
    """The invocation audit must read the route the API actually serves."""

    def test_invocation_audit_queries_the_real_route(self):
        verifier = _verifier(server_id="server-1", warmup_run_id="warmup",
                             run_outcome={"run-a": "success"})
        seen: list[tuple[str, str]] = []

        async def request(method: str, path: str, **_kwargs: object):
            seen.append((method, path))
            return _response({"items": [], "total": 0})

        verifier.request = request  # type: ignore[method-assign]
        with self.assertRaises(external.CheckFailure):
            asyncio.run(verifier.verify_invocations())
        self.assertEqual({path for _, path in seen}, {"/audit-logs/external-invocations"})
        # The base verifier prefixes /api/v1, so the composed URL is the real
        # admin router path and not a second, nonexistent /admin prefix.
        self.assertEqual(verifier.base + "/api/v1/audit-logs/external-invocations",
                         "http://127.0.0.1:1/api/v1/audit-logs/external-invocations")

    def test_the_measured_distribution_includes_the_warm_up_success(self):
        """The warm-up run is a real invocation, so it is one extra success."""
        rows = [_invocation("warmup", "succeeded", 12)]
        rows += [_invocation(f"success-{index}", "succeeded", 12)
                 for index in range(external.SUCCESS_RUNS)]
        rows += [_invocation(f"error-{index}", "tool_error", 20)
                 for index in range(external.FAILURE_RUNS)]
        rows += [_invocation(f"timeout-{index}", "timeout", 9_500)
                 for index in range(external.TIMEOUT_RUNS)]
        verifier = _verifier(server_id="server-1", warmup_run_id="warmup", run_outcome={
            f"success-{index}": "success" for index in range(external.SUCCESS_RUNS)} | {
            f"error-{index}": "tool_error" for index in range(external.FAILURE_RUNS)} | {
            f"timeout-{index}": "timeout" for index in range(external.TIMEOUT_RUNS)})

        async def request(_method: str, _path: str, **_kwargs: object):
            return _response({"items": rows, "total": len(rows)})

        verifier.request = request  # type: ignore[method-assign]
        asyncio.run(verifier.verify_invocations())
        self.assertEqual(verifier.metrics["invocation_status_counts"],
                         {"succeeded": external.SUCCESS_RUNS + 1,
                          "tool_error": external.FAILURE_RUNS,
                          "timeout": external.TIMEOUT_RUNS})
        self.assertEqual(verifier.metrics["invocation_duration_ms"],
                         {"max": 9_500, "min": 12})

    def test_a_missing_warm_up_invocation_fails_the_count(self):
        rows = [_invocation("run-a", "succeeded", 12)]
        verifier = _verifier(server_id="server-1", warmup_run_id="warmup",
                             run_outcome={"run-a": "success"})

        async def request(_method: str, _path: str, **_kwargs: object):
            return _response({"items": rows, "total": len(rows)})

        verifier.request = request  # type: ignore[method-assign]
        with self.assertRaises(external.CheckFailure) as caught:
            asyncio.run(verifier.verify_invocations())
        self.assertIn("expected 2 invocation records, found 1", str(caught.exception))

    def test_timeout_invocation_must_have_waited_for_the_deadline(self):
        rows = [_invocation("warmup", "succeeded", 12), _invocation("run-a", "timeout", 400)]
        verifier = _verifier(server_id="server-1", warmup_run_id="warmup",
                             run_outcome={"run-a": "timeout"})

        async def request(_method: str, _path: str, **_kwargs: object):
            return _response({"items": rows, "total": len(rows)})

        verifier.request = request  # type: ignore[method-assign]
        with self.assertRaises(external.CheckFailure) as caught:
            asyncio.run(verifier.verify_invocations())
        self.assertIn("did not wait for the configured deadline", str(caught.exception))

    def test_invocation_duration_may_not_run_far_past_the_deadline(self):
        rows = [_invocation("warmup", "succeeded", 12),
                _invocation("run-a", "timeout",
                            external.CALL_DEADLINE_MS + external.DEADLINE_OVERHEAD_MS + 1)]
        verifier = _verifier(server_id="server-1", warmup_run_id="warmup",
                             run_outcome={"run-a": "timeout"})

        async def request(_method: str, _path: str, **_kwargs: object):
            return _response({"items": rows, "total": len(rows)})

        verifier.request = request  # type: ignore[method-assign]
        with self.assertRaises(external.CheckFailure) as caught:
            asyncio.run(verifier.verify_invocations())
        self.assertIn("exceeds the configured call deadline", str(caught.exception))

    def test_unstructured_invocation_status_is_rejected(self):
        rows = [_invocation("warmup", "succeeded", 12), _invocation("run-a", "weird", 12)]
        verifier = _verifier(server_id="server-1", warmup_run_id="warmup",
                             run_outcome={"run-a": "success"})

        async def request(_method: str, _path: str, **_kwargs: object):
            return _response({"items": rows, "total": len(rows)})

        verifier.request = request  # type: ignore[method-assign]
        with self.assertRaises(external.CheckFailure) as caught:
            asyncio.run(verifier.verify_invocations())
        self.assertIn("not a structured value", str(caught.exception))


class FixtureStatisticsControls(unittest.TestCase):
    """The lease bound is about concurrent calls, not concurrent requests."""

    def test_fixture_statistics_require_the_call_peak(self):
        verifier = _verifier(acceptance=SimpleNamespace(
            fixture_stats=lambda: json.dumps({"requests": 3, "tool_calls": 2,
                                              "peak_in_flight": 3, "sessions": 1})))
        with self.assertRaises(external.CheckFailure) as caught:
            asyncio.run(verifier.fixture_stats())
        self.assertIn("peak_tools_in_flight", str(caught.exception))

    def test_fixture_statistics_accept_a_complete_snapshot(self):
        verifier = _verifier(acceptance=SimpleNamespace(fixture_stats=lambda: json.dumps(
            {"requests": 9, "tool_calls": 3, "peak_in_flight": 6,
             "peak_tools_in_flight": 3, "sessions": 3})))
        snapshot = asyncio.run(verifier.fixture_stats())
        self.assertEqual(snapshot["peak_tools_in_flight"], 3)

    @unittest.skipIf(settings is None, "backend package is not importable")
    def test_concurrency_window_matches_the_configured_lease(self):
        # The harness window is the product's own per-server bound. If the
        # setting moves without the harness following, the acceptance would
        # silently assert against a lease that no longer exists.
        self.assertEqual(external.MAX_CONCURRENCY_PER_SERVER,
                         settings.mcp_max_concurrency_per_server)
        self.assertGreaterEqual(external.MAX_CONCURRENCY_PER_SERVER, 2)


class FixtureDeadlineControls(unittest.TestCase):
    """The fixture retires a call on the deadline its caller actually used.

    A ``tools/call`` handler that outlives its client holds no lease, so the
    fixture has to know how long the Worker waits before it stops counting that
    call. The fixture's window starts when the request *arrives*, which is after
    the Worker's deadline started, so the flag must be the Worker's timeout minus
    the pre-arrival overhead. An equal window keeps counting an abandoned call
    after its slot was reused and reads as one call over the lease bound.
    """

    def resolve(self) -> dict:
        environment = {key: os.environ[key] for key in ("PATH", "HOME") if key in os.environ}
        environment.update({
            "AW_VERIFY_PASSWORD": "compose-control-only-password",
            "AW_VERIFY_JWT_SECRET": "compose-control-only-jwt",
            "AW_VERIFY_INVITE_CODE": "compose-control-only-invite",
            "AW_VERIFY_FIXTURE_TLS_DIR": tempfile.gettempdir() + "/aw-003-compose-control",
        })
        result = subprocess.run(
            ["docker", "compose", "--env-file", "/dev/null", "-p", "aw-003-controls",
             "-f", str(ROOT / "deploy" / "docker-compose.verify.yml"),
             "-f", str(ROOT / "deploy" / "docker-compose.external.yml"),
             "--profile", "external", "config", "--format", "json"],
            cwd=ROOT, env=environment, capture_output=True, text=True, check=True, timeout=60)
        return json.loads(result.stdout)["services"]

    def test_fixture_deadline_is_the_worker_timeout_minus_the_arrival_overhead(self):
        services = self.resolve()
        command = services["mcp-fixture"]["command"]
        self.assertIn("--client-deadline-seconds", command)
        deadline = float(command[command.index("--client-deadline-seconds") + 1])
        worker_timeout = float(services["worker"]["environment"]["MCP_CALL_TIMEOUT_SECONDS"])
        self.assertEqual(worker_timeout, external.CALL_DEADLINE_MS / 1000)
        self.assertEqual(deadline, worker_timeout - external.CLIENT_DEADLINE_ALLOWANCE_S)
        # The allowance has to be big enough to cover a handshake and small
        # enough that the fixture still observes the bound over most of a call.
        self.assertGreater(external.CLIENT_DEADLINE_ALLOWANCE_S, 0)
        self.assertLessEqual(external.CLIENT_DEADLINE_ALLOWANCE_S, worker_timeout / 2)


class RunOwnershipControls(unittest.TestCase):
    """A run must be read as the account that owns it.

    The API answers 404 for a run owned by anyone else, deliberately, so that
    existence is not disclosed. Polling a member's run as the governance default
    therefore fails on a run that exists and is behaving exactly as required --
    which is what the fail-closed check did before it was pinned here.
    """

    def _polling_verifier(self, seen: list[str]) -> external.ExternalVerifier:
        async def request(_method: str, _path: str, *, identity: str = "admin", **_kwargs: object):
            seen.append(identity)
            return _response({"status": "failed"})

        verifier = _verifier()
        verifier.request = request  # type: ignore[method-assign]
        return verifier

    def test_wait_run_polls_as_the_owning_account(self):
        seen: list[str] = []
        run = asyncio.run(self._polling_verifier(seen).wait_run(
            "run-1", expected_status="failed", identity="other"))
        self.assertEqual(run["status"], "failed")
        self.assertEqual(seen, ["other"])

    def test_wait_run_keeps_the_verifiers_own_identity_by_default(self):
        seen: list[str] = []
        asyncio.run(self._polling_verifier(seen).wait_run("run-1", expected_status="failed"))
        # ExternalVerifier defaults every request to the governance account, and
        # wait_run must not override that when no identity is named.
        self.assertEqual(seen, ["admin"])


class OutcomeEvidenceControls(unittest.TestCase):
    """A failed submission must leave a diagnosable, bounded reason."""

    def test_failed_submission_keeps_a_bounded_reason(self):
        verifier = _verifier(task_runs={0: "run-a"}, run_outcome={"run-a": "success"})
        facts = verifier.collect_outcomes([external.CheckFailure("x" * 500)])
        self.assertEqual(facts[0]["outcome"], "task_error")
        self.assertEqual(facts[0]["exception_type"], "CheckFailure")
        self.assertTrue(facts[0]["exception_message"])
        self.assertLessEqual(len(facts[0]["exception_message"]), 200)

    def test_successful_submission_records_its_terminal_state(self):
        verifier = _verifier(task_runs={0: "run-a"}, run_outcome={"run-a": "success"})
        facts = verifier.collect_outcomes([{"status": "completed", "error": None,
                                            "result": {"answer": "EXTERNAL_SUCCESS_OK"}}])
        self.assertEqual(facts[0]["run_status"], "completed")
        self.assertEqual(facts[0]["answer_tail"], "EXTERNAL_SUCCESS_OK")


class BuildRetryControls(unittest.TestCase):
    """A transient registry outage must not abort the whole acceptance."""

    def _acceptance(self) -> external.ExternalAcceptance:
        with tempfile.TemporaryDirectory(prefix="aw-003-retry-") as directory:
            pass
        return external.ExternalAcceptance(
            SimpleNamespace(output=str(Path(directory)), project="aw-003-controls"))

    def test_transient_registry_failure_is_retried(self):
        acceptance = self._acceptance()
        attempts: list[list[str]] = []

        def command(argv: list[str], **_kwargs: object):
            attempts.append(argv)
            if len(attempts) < 3:
                raise external.CheckFailure("Service Unavailable")
            return SimpleNamespace(returncode=0)

        acceptance.command = command  # type: ignore[method-assign]
        acceptance.build_with_retry("backend", ["docker", "build", "-t", "x"])
        self.assertEqual(len(attempts), 3)

    def test_persistent_failure_still_aborts_the_acceptance(self):
        acceptance = self._acceptance()

        def command(_argv: list[str], **_kwargs: object):
            raise external.AcceptanceFailure("registry is down")

        acceptance.command = command  # type: ignore[method-assign]
        with self.assertRaises(external.AcceptanceFailure):
            acceptance.build_with_retry("backend", ["docker", "build"], attempts=2)


class AcceptanceControls(unittest.TestCase):
    """Scratch-directory lifecycle for controls that build a real acceptance.

    ``ExternalAcceptance.__init__`` creates its own work directory, so a control
    only has to hand it a unique path. ``tearDown`` removes that scratch
    directory together with the ``aw-003-tls-*`` directory ``generate_tls``
    makes, so a control run leaves nothing behind in the shared temporary
    directory.
    """

    scratch_prefix = "aw-003-control-"

    def acceptance(self) -> external.ExternalAcceptance:
        return external.ExternalAcceptance(
            SimpleNamespace(output=tempfile.mkdtemp(prefix=self.scratch_prefix),
                            project="aw-003-controls"))

    def tearDown(self) -> None:
        for pattern in (self.scratch_prefix + "*", "aw-003-tls-*"):
            for path in Path(tempfile.gettempdir()).glob(pattern):
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)


class FixtureTlsPermissionControls(AcceptanceControls):
    """The Worker reads the fixture CA from a bind mount it does not own.

    The first CI run of this acceptance failed with "own Celery worker did not
    become ready", and the artifact said nothing else. The cause was here:
    ``mkdtemp`` leaves the directory 0700 and owned by whoever ran the
    acceptance, the Worker runs as uid 10001, and a native Linux daemon enforces
    the host's uid and mode on a bind mount -- so ``SSL_CERT_FILE`` pointed at a
    file the Worker could not read, ``ssl.create_default_context`` raised while
    the app was being imported (litellm builds its httpx client at import time),
    and the Worker exited with status 2 before it could answer a ping. Docker
    Desktop serves bind mounts permissively, so only a native run exposes it,
    which is why the mode bits are pinned here instead of being left to the next
    real-stack rerun.
    """

    def test_generated_tls_material_is_readable_by_the_container_user(self):
        acceptance = self.acceptance()
        acceptance.generate_tls()
        directory = acceptance.tls_dir
        assert directory is not None
        directory_mode = stat.S_IMODE(directory.stat().st_mode)
        self.assertTrue(directory_mode & stat.S_IXOTH,
                        f"fixture TLS directory is not traversable by the Worker: "
                        f"{oct(directory_mode)}")
        bundle_mode = stat.S_IMODE((directory / "ca-bundle.pem").stat().st_mode)
        self.assertTrue(bundle_mode & stat.S_IROTH,
                        f"fixture CA bundle is not readable by the Worker: {oct(bundle_mode)}")
        certificate_mode = stat.S_IMODE((directory / "fixture.crt").stat().st_mode)
        self.assertTrue(certificate_mode & stat.S_IROTH,
                        f"fixture certificate is not readable by the Worker: "
                        f"{oct(certificate_mode)}")
        key_mode = stat.S_IMODE((directory / "fixture.key").stat().st_mode)
        self.assertFalse(key_mode & stat.S_IROTH,
                         f"fixture private key must stay unreadable to others: {oct(key_mode)}")

    def test_the_trust_bundle_carries_the_system_roots_too(self):
        """Relaxing the trust store to pass would be the forbidden shortcut."""
        import certifi

        acceptance = self.acceptance()
        acceptance.generate_tls()
        directory = acceptance.tls_dir
        assert directory is not None
        bundle = (directory / "ca-bundle.pem").read_text()
        self.assertTrue(bundle.startswith(Path(certifi.where()).read_text()))
        self.assertIn("BEGIN CERTIFICATE", bundle)


class ReadinessEvidenceControls(AcceptanceControls):
    """A Worker that never answers must leave a diagnosable artifact."""

    scratch_prefix = "aw-003-evidence-control-"

    def test_a_readiness_failure_records_bounded_evidence(self):
        acceptance = self.acceptance()

        def compose(*args: str, **_kwargs: object):
            if args[:1] == ("exec",):
                return SimpleNamespace(returncode=69, stdout=b"Error: No nodes replied\n")
            if args[:1] == ("ps",):
                return SimpleNamespace(returncode=0, stdout=b"container-id\n")
            return SimpleNamespace(returncode=0, stdout=b"celery@host v5.7.0\nPermissionError\n")

        acceptance.compose = compose  # type: ignore[method-assign]
        acceptance.command = lambda *_args, **_kwargs: SimpleNamespace(  # type: ignore[method-assign]
            returncode=0, stdout=b"exited exit=2 restarts=0 oom=false\n")
        with mock.patch.object(external, "WORKER_READY_TIMEOUT_S", 0.05), \
                self.assertRaises(external.AcceptanceFailure):
            acceptance.wait_worker("worker")
        entry = acceptance.results[-1]
        self.assertEqual(entry["name"], "worker_readiness")
        self.assertFalse(entry["passed"])
        self.assertEqual(entry["container_state"], ["exited exit=2 restarts=0 oom=false"])
        self.assertIn("PermissionError", entry["worker_log_tail"])
        self.assertEqual(entry["ping_reply"], ["Error: No nodes replied"])

    def test_bounded_lines_redacts_generated_secrets_and_url_credentials(self):
        acceptance = self.acceptance()
        secret = "003-fixture-" + "z" * 30
        acceptance.compose_env["AW_VERIFY_FIXTURE_SECRET"] = secret
        lines = acceptance.bounded_lines(
            f"Authorization: Bearer {secret}\n"
            "transport: redis://worker:s3cret-pw@redis:6379/1\n"
            "transport: redis://:**@redis:6379/2\n"
            "celery@host ready.\n")
        self.assertNotIn(secret, " ".join(lines))
        self.assertNotIn("s3cret-pw", " ".join(lines))
        self.assertIn("Authorization: Bearer <redacted>", lines)
        self.assertIn("transport: redis://<redacted>@redis:6379/1", lines)
        # Celery already masks its own broker password; that must survive.
        self.assertIn("transport: redis://:**@redis:6379/2", lines)

    def test_bounded_lines_keeps_only_the_tail_and_truncates_each_line(self):
        acceptance = self.acceptance()
        text = "\n".join(f"line-{index}" for index in range(50))
        self.assertEqual(acceptance.bounded_lines(text, lines=3),
                         ["line-47", "line-48", "line-49"])
        self.assertEqual(acceptance.bounded_lines("x" * 900, lines=1), ["x" * 300])
        self.assertEqual(acceptance.bounded_lines("a\n\n\nb", lines=5), ["a", "b"])


class P95StrictnessControls(unittest.TestCase):
    """The harness's own copy of the SC-004 verdict cannot be relaxed."""

    def verdict(self, baseline, loaded, failures=0, control=(100.0, 100.0), control_failures=0):
        """Verdict against an idle control unless the test says otherwise."""
        return external.p95_verdict(
            baseline, loaded, failures,
            control_baseline=[control[0]] * 100, control_loaded=[control[1]] * 100,
            control_failures=control_failures,
        )

    def test_ratio_limit_and_zero_failure_requirement(self):
        # Latencies above the ceiling, so the ratio is what decides.
        baseline = [100.0 + value for value in range(100)]
        verdict = self.verdict(baseline, baseline)
        self.assertTrue(verdict["passed"])
        self.assertEqual(verdict["p95_ratio"], 1.0)
        self.assertFalse(self.verdict(baseline, baseline, 1)["passed"])
        self.assertFalse(self.verdict(
            baseline, [value * (external.P95_RATIO_LIMIT + 0.01) for value in baseline])["passed"])

    def test_ratio_noise_at_millisecond_latency_is_not_a_failure(self):
        """The same commit measured 1.42, 1.74 and 2.17 on one runner."""
        result = self.verdict([8.0] * 100, [32.0] * 100, control=(3.0, 4.9))
        self.assertGreater(result["attributed_p95_ratio"], external.P95_RATIO_LIMIT)
        self.assertLess(result["load_p95_ms"], external.P95_ABSOLUTE_CEILING_MS)
        self.assertTrue(result["passed"])

    def test_a_control_cannot_absorb_a_real_endpoint_regression(self):
        """The control explains host contention, not a defect in this endpoint."""
        baseline = [10.0] * 100
        shared_host = self.verdict(baseline, [22.0] * 100, control=(10.0, 20.0))
        self.assertAlmostEqual(shared_host["control_p95_ratio"], 2.0)
        self.assertAlmostEqual(shared_host["attributed_p95_ratio"], 1.1)
        self.assertTrue(shared_host["passed"])
        slow_baseline = [100.0] * 100
        worse_than_the_host = self.verdict(slow_baseline, [400.0] * 100, control=(100.0, 200.0))
        self.assertAlmostEqual(worse_than_the_host["attributed_p95_ratio"], 2.0)
        self.assertFalse(worse_than_the_host["passed"])

    def test_the_control_must_be_present_and_failure_free(self):
        baseline = [10.0] * 100
        self.assertFalse(self.verdict(baseline, baseline, control_failures=1)["passed"])
        with self.assertRaises(external.CheckFailure):
            external.p95_verdict(baseline, baseline, 0,
                                 control_baseline=[], control_loaded=[], control_failures=0)

    def test_the_verdict_records_the_shape_of_both_measurements(self):
        baseline = [float(value) for value in range(1, 101)]
        # Ten outliers in a hundred is more than the top five percent, so this
        # fails the gate -- and the recorded shape shows *why*: p90 is still
        # normal while p95 jumps, which is a handful of slow samples rather than
        # a uniform shift. Both produce the same ratio and need different fixes.
        verdict = self.verdict(baseline, [50.0] * 90 + [500.0] * 10)
        self.assertFalse(verdict["passed"])
        self.assertEqual(verdict["load_distribution"]["p50"], 50.0)
        self.assertEqual(verdict["load_distribution"]["p90"], 50.0)
        self.assertEqual(verdict["load_distribution"]["p95"], 500.0)
        self.assertEqual(verdict["load_distribution"]["max"], 500.0)
        self.assertEqual(verdict["load_distribution"]["min"], 50.0)
        # Nearest rank, never an interpolated midpoint: the median of 1..100 is
        # the 50th value, not 50.5.
        self.assertEqual(verdict["baseline_distribution"]["p50"], 50.0)
        self.assertEqual(verdict["baseline_distribution"]["max"], 100.0)
        self.assertEqual(set(verdict["baseline_distribution"]),
                         {"min", "p50", "p90", "p95", "p99", "max"})

    def test_baseline_sample_count_is_fixed(self):
        self.assertEqual(external.BASELINE_SAMPLES, 100)

    def test_percentile_is_nearest_rank(self):
        self.assertEqual(external.percentile95(list(range(1, 101))), 95.0)


if __name__ == "__main__":
    unittest.main()
