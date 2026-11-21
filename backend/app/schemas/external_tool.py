"""Safe request/response models for governed external MCP tools."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
    model_validator,
)

from app.executors.stdio_mcp_client import DEFAULT_ALLOWED_COMMANDS, is_allowed_command


class CredentialWrite(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    kind: Literal["bearer", "api_key"]
    value: str = Field(min_length=1, max_length=8192, repr=False)
    header_name: str | None = Field(default=None, max_length=100, repr=False)


class CredentialOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    label: str
    kind: Literal["bearer", "api_key"]
    header_name: str
    status: Literal["active", "revoked", "superseded"]
    key_version: str
    last_used_at: datetime | None = None


class ExternalServerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=4000)
    # 024：http = Streamable HTTP（默认，兼容既有调用方）；stdio = 本地子进程
    transport: Literal["http", "stdio"] = "http"
    endpoint_url: str | None = Field(default=None, max_length=2048)
    # stdio 的启动参数（数组，绝不拼 shell 字符串）
    command: str | None = Field(default=None, max_length=200)
    args: list[str] = Field(default_factory=list, max_length=32)
    # 只声明**变量名**，值在运行时从 worker 环境取（不落库）
    env_keys: list[str] = Field(default_factory=list, max_length=16)
    source_url: str | None = Field(default=None, max_length=2048)
    maintainer: str = Field(min_length=1, max_length=100)
    auth_mode: Literal["none", "static_bearer", "static_api_key"]
    credential: CredentialWrite | None = None

    @model_validator(mode="after")
    def validate_transport(self) -> ExternalServerCreate:
        """两种传输互斥校验（024）。

        stdio 没有 URL、也没有 HTTP 头，所以：必须给 command、命令要在白名单里、
        auth_mode 必须是 none、endpoint_url 由服务端补成 `stdio://<cmd>` 占位。
        http 则必须给合法 URL，且不允许带 stdio 的参数（避免半配置）。
        """
        if self.transport == "stdio":
            if not self.command:
                raise ValueError("stdio 传输必须提供 command")
            base = os.path.basename(self.command)
            if not is_allowed_command(self.command):
                raise ValueError(
                    "command 不在白名单内（允许："
                    + ", ".join(DEFAULT_ALLOWED_COMMANDS)
                    + "）"
                )
            if self.auth_mode != "none":
                raise ValueError("stdio 传输不支持 HTTP 凭证")
            if self.endpoint_url is None:
                self.endpoint_url = f"stdio://{base}"
        else:
            if self.command or self.args:
                raise ValueError("http 传输不接受 command/args")
            if not self.endpoint_url:
                raise ValueError("http 传输必须提供 endpoint_url")
            HttpUrl(self.endpoint_url)
        return self

    @field_validator("credential")
    @classmethod
    def credential_matches_auth(cls, value: CredentialWrite | None, info):
        auth_mode = info.data.get("auth_mode")
        if auth_mode == "none" and value is not None:
            raise ValueError("anonymous server must not include credentials")
        if auth_mode != "none" and value is None:
            raise ValueError("configured authentication requires a credential")
        if (
            value is not None
            and auth_mode == "static_bearer"
            and value.kind != "bearer"
        ):
            raise ValueError("static_bearer requires a bearer credential")
        if (
            value is not None
            and auth_mode == "static_api_key"
            and value.kind != "api_key"
        ):
            raise ValueError("static_api_key requires an api_key credential")
        return value


class ExternalServerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, min_length=1, max_length=4000)
    endpoint_url: str | None = Field(default=None, min_length=1, max_length=2048)
    source_url: str | None = Field(default=None, max_length=2048)
    maintainer: str | None = Field(default=None, min_length=1, max_length=100)


class ValidationSummary(BaseModel):
    id: str
    status: Literal["passed", "failed", "partial"]
    connection_status: str
    auth_status: str
    discovery_status: str
    discovered_count: int = Field(ge=0)
    valid_count: int = Field(ge=0)
    safe_error: str | None = None
    started_at: datetime
    completed_at: datetime | None = None


class ExternalServerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    # 024：传输形态与 stdio 启动参数（前端展示 + 排查用）
    transport: str = "http"
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env_keys: list[str] = Field(default_factory=list)

    id: str
    owner_id: str
    name: str
    description: str
    endpoint_url: str
    source_url: str | None
    maintainer: str
    auth_mode: str
    status: str
    created_at: datetime
    updated_at: datetime
    last_validated_at: datetime | None
    current_validation_status: str | None
    credentials: list[CredentialOut] = Field(default_factory=list)
    tools: list[dict[str, Any]] = Field(default_factory=list)
    last_validation: ValidationSummary | None = None


class ExternalToolVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    server_id: str
    external_name: str
    stable_name: str
    version: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None
    capability_category: str | None
    required_permissions: list[str]
    definition_hash: str
    availability: str
    discovered_at: datetime


class ListingOut(BaseModel):
    listing_id: str
    tool_version_id: str
    stable_name: str
    display_name: str
    description: str
    server_name: str
    version: str
    permissions: list[str] = Field(default_factory=list)
    restrictions: dict[str, Any] = Field(default_factory=dict)
    status: str
    validated_at: datetime | None = None


class ListingList(BaseModel):
    items: list[ListingOut]
    total: int


class ReviewDecision(BaseModel):
    reason: str = Field(min_length=5, max_length=2000)


class ToolInstallCreate(BaseModel):
    version_policy: Literal["pinned", "allow_patch"] = "pinned"


class ToolInstallationOut(BaseModel):
    id: str
    listing_id: str
    owner_id: str
    status: str
    version_policy: str
    installed_at: datetime


class ExternalGrantCreate(BaseModel):
    installation_id: str
    tool_version_id: str
    credential_id: str | None = None
    permissions: list[str] = Field(default_factory=list, max_length=100)


class ExternalGrantOut(BaseModel):
    id: str
    agent_id: str
    installation_id: str
    tool_version_id: str
    stable_name: str
    version: str
    permissions: list[str]
    credential: CredentialOut | None = None
    status: str
    unavailable_reason: str | None = None


class ExternalGrantList(BaseModel):
    items: list[ExternalGrantOut]
    total: int


class ExternalServerList(BaseModel):
    items: list[ExternalServerOut]
    total: int


class CredentialRotate(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    kind: Literal["bearer", "api_key"] | None = None
    value: str = Field(min_length=1, max_length=8192, repr=False)


class InstallationUpgrade(BaseModel):
    listing_id: str
    reason: str = Field(min_length=5, max_length=2000)
