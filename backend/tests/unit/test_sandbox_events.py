"""Sandbox event chunks are byte-bounded and lossless for UTF-8."""

from app.tasks import events


def test_output_chunks_are_utf8_byte_bounded_without_losing_characters(monkeypatch):
    sent = []
    monkeypatch.setattr(
        events, "_publish", lambda run_id, payload: sent.append(payload)
    )
    text = "你好🌍" * 3000
    events.sandbox_output("run", "execution", "stdout", text)
    assert len(sent) > 1
    assert all(0 < len(e["chunk"].encode("utf-8")) <= 4096 for e in sent)
    assert "".join(e["chunk"] for e in sent) == text
    assert all(e["execution_id"] == "execution" for e in sent)


def test_denial_event_explains_policy_without_exposing_rule_internals(monkeypatch):
    sent = []
    monkeypatch.setattr(
        events, "_publish", lambda run_id, payload: sent.append(payload)
    )
    events.egress_denied(
        "run",
        "execution",
        target_ip="203.0.113.4",
        target_domain="ip-direct",
        port=443,
        matched_rule_id=None,
    )
    assert "策略" in sent[0]["reason"]
