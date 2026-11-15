"""Tests for DNS-to-conntrack correlation and access records."""

from app.services.egress_service import merge_connection_records


def test_merge_uses_nearest_dns_query_for_shared_ip() -> None:
    queries = [
        {"domain": "one.example", "resolved_ip": "203.0.113.7", "timestamp": 10.0},
        {"domain": "two.example", "resolved_ip": "203.0.113.7", "timestamp": 19.0},
    ]
    connections = [
        {"target_ip": "203.0.113.7", "port": 443, "timestamp": 19.5},
        {"target_ip": "203.0.113.7", "port": 8443, "timestamp": 10.5},
    ]

    merged = merge_connection_records(queries, connections, window_seconds=5)

    assert [item["target_domain"] for item in merged] == ["two.example", "one.example"]


def test_merge_preserves_distinct_connections_to_the_same_destination() -> None:
    connections = [
        {"target_ip": "203.0.113.7", "port": 443, "source_port": 41001, "timestamp": 19.5},
        {"target_ip": "203.0.113.7", "port": 443, "source_port": 41002, "timestamp": 19.6},
    ]
    merged = merge_connection_records([], connections, window_seconds=5)
    assert len(merged) == 2
    assert [r["source_port"] for r in merged] == [41001, 41002]


def test_merge_marks_direct_ip_and_ignores_queries_outside_window() -> None:
    queries = [
        {"domain": "stale.example", "resolved_ip": "198.51.100.8", "timestamp": 1.0}
    ]
    connections = [
        {"target_ip": "198.51.100.8", "port": 443, "timestamp": 100.0},
        {"target_ip": "198.51.100.9", "port": 443, "timestamp": 100.0},
    ]

    merged = merge_connection_records(queries, connections, window_seconds=5)

    assert [item["target_domain"] for item in merged] == ["ip-direct", "ip-direct"]
    assert [item["target_ip"] for item in merged] == ["198.51.100.8", "198.51.100.9"]
