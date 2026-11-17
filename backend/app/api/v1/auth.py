"""认证端点（T023 + T027 审计挂接）。

- POST /auth/register → 201
- POST /auth/login → 200
- POST /auth/refresh → 200
- GET /auth/me → 200
"""

from fastapi import APIRouter, Request

from app.api.deps import DB, CurrentUser
from app.api.errors import AppError
from app.api.middleware import client_ip
from app.core import login_limiter
from app.core.audit import write_audit
from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    RefreshRequest,
    RefreshResponse,
    RegisterRequest,
    RegisterResponse,
    TokenPair,
    UserOut,
)
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=RegisterResponse, status_code=201)
async def register(payload: RegisterRequest, db: DB):
    user, tokens = await auth_service.register(
        db,
        email=str(payload.email),
        password=payload.password,
        invite_code=payload.invite_code,
    )
    await write_audit(
        db, user_id=user.id, action="user_register", target_type="user", target_id=user.id
    )
    await db.commit()  # 审计落库（register 业务已在 service 内提交）
    return {"user": UserOut.model_validate(user), "tokens": TokenPair(**tokens)}


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest, request: Request, db: DB):
    """登录：先过防爆破限流，再校验密码。

    失败会计数（按邮箱+IP 与按 IP 两层），成功则清掉该账号的失败计数。
    限流只挡失败，正常登录不受影响；Redis 不可用时放行（见 login_limiter）。
    """
    email = str(payload.email)
    ip = client_ip(request)

    await login_limiter.check_login_allowed(email=email, ip=ip)
    try:
        user, tokens = await auth_service.login(db, email=email, password=payload.password)
    except AppError as exc:
        # 仅「凭证错误/账号禁用」计入爆破计数；参数校验类错误不算（打错格式不等于猜密码）
        if exc.code == "FORBIDDEN":
            await login_limiter.record_login_failure(email=email, ip=ip)
        raise

    await login_limiter.reset_login_failures(email=email, ip=ip)

    await write_audit(
        db, user_id=user.id, action="user_login", target_type="user", target_id=user.id
    )
    await db.commit()  # 审计落库（login 无业务写操作，必须显式提交）
    return {"user": UserOut.model_validate(user), "tokens": TokenPair(**tokens)}


@router.post("/refresh", response_model=RefreshResponse)
async def refresh(payload: RefreshRequest, db: DB):
    tokens = await auth_service.refresh(db, refresh_token=payload.refresh_token)
    return {"tokens": TokenPair(**tokens)}


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser):
    return UserOut.model_validate(user)
