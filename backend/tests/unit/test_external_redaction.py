from app.services.external_audit_service import _safe_value


def test_external_audit_redacts_nested_secrets_and_bounds_payloads():
    safe = _safe_value(
        {
            "server_id": "server-1",
            "authorization": "canary-secret",
            "request": {"api_key": "canary-secret", "query": "safe"},
            "items": ["x" * 1000],
        }
    )
    assert safe["authorization"] == "[redacted]"
    assert safe["request"]["api_key"] == "[redacted]"
    assert safe["request"]["query"] == "safe"
    assert len(safe["items"][0]) == 512
    assert "canary-secret" not in repr(safe)
