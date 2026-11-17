"""日志脱敏单元测试：敏感字段不得出现在日志里。

此前没有任何强制脱敏，靠人记得别打印 token；一旦某处 logger.info(...,
authorization=...)，密钥就会进日志/被采集。
"""

from app.core.log_setup import redact_processor


def _redact(event: dict) -> dict:
    return redact_processor(None, "info", event)


class TestRedaction:
    def test_top_level_secrets_redacted(self):
        out = _redact(
            {
                "event": "login",
                "password": "P@ssw0rd",
                "token": "eyJhbGci...",
                "api_key": "sk-xxx",
                "authorization": "Bearer abc",
            }
        )
        assert out["password"] == "***"
        assert out["token"] == "***"
        assert out["api_key"] == "***"
        assert out["authorization"] == "***"
        assert out["event"] == "login"

    def test_nested_structures_redacted(self):
        out = _redact(
            {
                "event": "payload",
                "detail": {
                    "user": {"password_hash": "$2b$12$..."},
                    "refresh_token": "r",
                },
                "items": [{"secret": "s1"}, {"ok": 1}],
            }
        )
        assert out["detail"]["user"]["password_hash"] == "***"
        assert out["detail"]["refresh_token"] == "***"
        assert out["items"][0]["secret"] == "***"
        assert out["items"][1]["ok"] == 1

    def test_non_sensitive_keys_untouched(self):
        out = _redact(
            {
                "event": "request",
                "request_id": "abc123",
                "user_id": "u-1",
                "status": 200,
                "duration_ms": 12.5,
                "path": "/api/v1/agents",
            }
        )
        assert out == {
            "event": "request",
            "request_id": "abc123",
            "user_id": "u-1",
            "status": 200,
            "duration_ms": 12.5,
            "path": "/api/v1/agents",
        }

    def test_key_match_is_case_insensitive_and_substring(self):
        out = _redact({"Access_Token": "a", "MINIO_SECRET_KEY": "b", "x-api-key": "c"})
        assert out == {
            "Access_Token": "***",
            "MINIO_SECRET_KEY": "***",
            "x-api-key": "***",
        }

    def test_depth_limit_does_not_crash(self):
        deep: dict = {"secret": "s"}
        node = deep
        for _ in range(10):
            child: dict = {}
            node["child"] = child
            node = child
        out = _redact(deep)
        assert out["secret"] == "***"  # 顶层仍脱敏，深层原样保留（不递归爆炸）
