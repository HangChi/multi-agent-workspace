"""认证端点（T023 + T027 审计挂接 + refresh token 走 HttpOnly cookie）。

- POST /auth/register → 201（同时下发 refresh cookie）
- POST /auth/login → 200（同时下发 refresh cookie）
- POST /auth/refresh → 200（消费 cookie 或请求体里的 refresh，轮换后重设 cookie）
- POST /auth/logout → 200（撤销 refresh 并清 cookie）
- GET  /auth/me → 200

为什么 refresh token 放在 HttpOnly cookie 里：
前端原先把它存在 localStorage，任何一次 XSS 都能读走一个 7 天有效的凭证 ——
等于长期账号接管。cookie + HttpOnly 让 JS 读不到它；配合 CSP（script-src 'self'）
才是完整的一对：CSP 降低 XSS 成功概率，HttpOnly 降低 XSS 成功后的损失。

跨站风险：cookie 用 SameSite=Lax，且 refresh/logout 只接受 POST；跨站表单提交
不会带上它，这两个端点也不改变用户可见状态。access token 仍走 Authorization 头
（不放 cookie），因此业务接口不受 CSRF 影响。

兼容性：响应体仍返回 tokens（含 refresh），供非浏览器客户端与既有测试使用；
浏览器端只读 access，refresh 由 cookie 承载。
"""

from fastapi import APIRouter, Request, Response

from app.api.deps import DB, CurrentUser
from app.api.errors import AppError
from app.api.middleware import client_ip
from app.core import login_limiter
from app.core.audit import write_audit
from app.core.config import settings
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

_REFRESH_TTL_SECONDS = settings.jwt_refresh_ttl_days * 24 * 3600


def _set_refresh_cookie(response: Response, token: str) -> None:
    """下发 refresh cookie。

    Secure 只在生产开：本地开发是 http，加了 Secure 浏览器会直接丢弃 cookie，
    表现为「登录成功但一刷新就掉线」，很难查。
    """
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=token,
        max_age=_REFRESH_TTL_SECONDS,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        path=settings.refresh_cookie_path,
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        path=settings.refresh_cookie_path,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
    )


def _refresh_token_from(request: Request, payload: RefreshRequest | None) -> str:
    """优先取请求体（脚本/旧客户端），否则取 cookie（浏览器）。"""
    if payload is not None and payload.refresh_token:
        return payload.refresh_token
    cookie = request.cookies.get(settings.refresh_cookie_name)
    if not cookie:
        raise AppError("UNAUTHORIZED", "缺少 refresh token，请重新登录")
    return cookie


@router.post("/register", response_model=RegisterResponse, status_code=201)
async def register(payload: RegisterRequest, response: Response, db: DB):
    user, tokens = await auth_service.register(
        db,
        email=str(payload.email),
        password=payload.password,
        invite_code=payload.invite_code,
    )
    await write_audit(
        db,
        user_id=user.id,
        action="user_register",
        target_type="user",
        target_id=user.id,
    )
    await db.commit()  # 审计落库（register 业务已在 service 内提交）
    _set_refresh_cookie(response, tokens["refresh"])
    return {"user": UserOut.model_validate(user), "tokens": TokenPair(**tokens)}


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest, request: Request, response: Response, db: DB):
    """登录：先过防爆破限流，再校验密码。

    失败会计数（按邮箱+IP 与按 IP 两层），成功则清掉该账号的失败计数。
    限流只挡失败，正常登录不受影响；Redis 不可用时放行（见 login_limiter）。
    """
    email = str(payload.email)
    ip = client_ip(request)

    await login_limiter.check_login_allowed(email=email, ip=ip)
    try:
        user, tokens = await auth_service.login(
            db, email=email, password=payload.password
        )
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
    _set_refresh_cookie(response, tokens["refresh"])
    return {"user": UserOut.model_validate(user), "tokens": TokenPair(**tokens)}


@router.post("/refresh", response_model=RefreshResponse)
async def refresh(
    request: Request,
    response: Response,
    db: DB,
    payload: RefreshRequest | None = None,
):
    token = _refresh_token_from(request, payload)
    tokens = await auth_service.refresh(db, refresh_token=token)
    # 轮换后必须重设 cookie：旧 refresh 已被消费，不重设浏览器就拿着废票
    _set_refresh_cookie(response, tokens["refresh"])
    return {"tokens": TokenPair(**tokens)}


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    payload: RefreshRequest | None = None,
):
    """登出：撤销 refresh 并清 cookie。

    幂等：没有 cookie / token 已失效也返回 200 —— 登出失败不该把用户困在页面里。
    """
    token = ""
    if payload is not None and payload.refresh_token:
        token = payload.refresh_token
    else:
        token = request.cookies.get(settings.refresh_cookie_name, "")
    if token:
        await auth_service.revoke(token)
    _clear_refresh_cookie(response)
    return {"status": "ok"}


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser):
    return UserOut.model_validate(user)
