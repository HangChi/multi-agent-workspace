"""Bounded counters and diagnostics for governed external MCP usage."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Any

_MAX_COUNTERS = 2048


class ExternalObservability:
    """Process-local bounded counters suitable for diagnostics and release alerts."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._counts: Counter[str] = Counter()
        self._last_seen: dict[str, datetime] = {}

    def increment(self, name: str, *, amount: int = 1) -> None:
        key = str(name)[:100]
        with self._lock:
            if key not in self._counts and len(self._counts) >= _MAX_COUNTERS:
                return
            self._counts[key] += max(0, amount)
            self._last_seen[key] = datetime.now(UTC)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "counters": dict(self._counts),
                "last_seen": {
                    key: value.isoformat() for key, value in self._last_seen.items()
                },
            }

    def recent_alerts(
        self, *, minutes: int = 60, now: datetime | None = None
    ) -> list[dict[str, Any]]:
        now = now or datetime.now(UTC)
        cutoff = now - timedelta(minutes=max(1, minutes))
        return [
            alert
            for alert in self.alerts(now=now)
            if datetime.fromisoformat(alert["last_seen"]) >= cutoff
        ]

    def alerts(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        now = now or datetime.now(UTC)
        with self._lock:
            counts = dict(self._counts)
            last_seen = dict(self._last_seen)
        alerts: list[dict[str, Any]] = []
        thresholds = {
            "validation_drift": 1,
            "auth_error": 5,
            "policy_denied": 10,
            "destination_change": 1,
            "result_truncated": 5,
            "audit_gap": 1,
        }
        for name, threshold in thresholds.items():
            count = counts.get(name, 0)
            if count >= threshold:
                alerts.append(
                    {
                        "name": name,
                        "count": count,
                        "last_seen": last_seen.get(name, now).isoformat(),
                    }
                )
        return alerts


observability = ExternalObservability()


def observe_invocation(status: str) -> None:
    observability.increment(status)
    if status in {"auth_error", "policy_denied", "result_truncated"}:
        observability.increment(status)


def observe_validation(*, drift: bool = False, audit_gap: bool = False) -> None:
    if drift:
        observability.increment("validation_drift")
    if audit_gap:
        observability.increment("audit_gap")


def observe_destination_change() -> None:
    observability.increment("destination_change")


def recent_alerts(*, minutes: int = 60) -> list[dict[str, Any]]:
    now = datetime.now(UTC)
    cutoff = now - timedelta(minutes=max(1, minutes))
    return [
        alert
        for alert in observability.alerts(now=now)
        if datetime.fromisoformat(alert["last_seen"]) >= cutoff
    ]
