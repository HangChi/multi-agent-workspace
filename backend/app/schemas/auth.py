"""认证相关 Pydantic schema（T021）。对齐 contracts/api.md 认证部分。"""

import re

from pydantic import BaseModel, EmailStr, Field, field_validator

# 密码强度：至少 8 位，含字母与数字
_PASSWORD_RE = re.compile(r"^(?=.*[A-Za-z])(?=.*\d).{8,}$")


class RegisterRequest(BaseModel):
    """注册请求：邮箱 + 密码 + 邀请码。"""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    invite_code: str = Field(min_length=4, max_length=64)

    @field_validator("password")
    @classmethod
    def _check_password_strength(cls, v: str) -> str:
        if not _PASSWORD_RE.match(v):
            raise ValueError("密码至少 8 位且同时包含字母与数字")
        return v


class LoginRequest(BaseModel):
    """登录请求。"""

    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    """刷新 token 请求。"""

    refresh_token: str


class TokenPair(BaseModel):
    """access + refresh token 对。"""

    access: str
    refresh: str


class UserOut(BaseModel):
    """用户公开信息。"""

    id: str
    email: EmailStr
    role: str
    status: str

    model_config = {"from_attributes": True}


class RegisterResponse(BaseModel):
    """注册响应。"""

    user: UserOut
    tokens: TokenPair


class LoginResponse(BaseModel):
    """登录响应。"""

    user: UserOut
    tokens: TokenPair


class RefreshResponse(BaseModel):
    """刷新响应。"""

    tokens: TokenPair
