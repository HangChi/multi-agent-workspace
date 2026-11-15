"""Bounded external usage diagnostics tests."""

from datetime import UTC, datetime, timedelta

from app.services.external_observability import ExternalObservability


def test_observability_counts_are_bounded_and_alertable():
    metrics = ExternalObservability()
    metrics.increment("auth_error", amount=5)
    metrics.increment("policy_denied", amount=10)
    metrics.increment("result_truncated", amount=5)
    snapshot = metrics.snapshot()
    assert snapshot["counters"] == {
        "auth_error": 5,
        "policy_denied": 10,
        "result_truncated": 5,
    }
    assert {item["name"] for item in metrics.alerts()} == {
        "auth_error",
        "policy_denied",
        "result_truncated",
    }


def test_observability_filters_stale_alerts():
    metrics = ExternalObservability()
    metrics.increment("validation_drift")
    old = datetime.now(UTC) - timedelta(hours=2)
    metrics._last_seen["validation_drift"] = old
    assert metrics.alerts(now=datetime.now(UTC))
