"""认证端点（T023 + T027 审计挂接）。

- POST /auth/register → 201
- POST /auth/login → 200
- POST /auth/refresh → 200
- GET /auth/me → 200
"""

from fastapi import APIRouter

from app.api.deps import DB, CurrentUser
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
async def login(payload: LoginRequest, db: DB):
    user, tokens = await auth_service.login(
        db, email=str(payload.email), password=payload.password
    )
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
