"""配置校验单元测试：生产环境拒绝弱默认密钥。

背景：T010 要求「启动时校验必需项」，此前缺失——忘了配 JWT_SECRET 时
应用会用代码内置默认值启动，等于鉴权形同虚设。这里锁定该行为。
"""

import pytest
from pydantic import ValidationError

from app.core.config import Settings

_STRONG_JWT = "a" * 48
_STRONG_INVITE = "INV-2026-SECURE-CODE"
_STRONG_MCP_KEY = "mcp-credential-key-2026-for-tests-strong"


def _settings(**overrides) -> Settings:
    """构造 Settings，屏蔽外部 .env/环境变量干扰。"""
    base = {
        "app_env": "development",
        "jwt_secret": "dev-only-change-me",
        "invite_code": "CHANGE-ME-ADMIN-CODE",
        "mcp_credential_key": _STRONG_MCP_KEY,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


class TestProductionSecrets:
    def test_development_allows_defaults(self):
        """开发环境允许默认值（本地起服务不需要先配密钥）。"""
        s = _settings()
        assert s.is_production is False
        assert s.jwt_secret == "dev-only-change-me"

    def test_production_rejects_default_jwt_secret(self):
        with pytest.raises(ValidationError) as exc:
            _settings(app_env="production", invite_code=_STRONG_INVITE)
        assert "JWT_SECRET" in str(exc.value)

    def test_production_rejects_short_jwt_secret(self):
        with pytest.raises(ValidationError) as exc:
            _settings(
                app_env="production",
                jwt_secret="short-secret",
                invite_code=_STRONG_INVITE,
            )
        assert "JWT_SECRET" in str(exc.value)

    def test_production_rejects_default_invite_code(self):
        with pytest.raises(ValidationError) as exc:
            _settings(app_env="production", jwt_secret=_STRONG_JWT)
        assert "INVITE_CODE" in str(exc.value)

    def test_production_accepts_strong_secrets(self):
        s = _settings(
            app_env="production", jwt_secret=_STRONG_JWT, invite_code=_STRONG_INVITE
        )
        assert s.is_production is True

    @pytest.mark.parametrize("env", ["production", "PRODUCTION", "prod"])
    def test_production_env_aliases(self, env: str):
        with pytest.raises(ValidationError):
            _settings(app_env=env, jwt_secret=_STRONG_JWT)

    def test_staging_is_not_treated_as_production(self):
        """非 production 语义的环境不做强校验（避免误伤 CI/staging）。"""
        s = _settings(app_env="staging")
        assert s.is_production is False
