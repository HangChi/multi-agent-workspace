"""统一错误处理：AppError 异常体系 + FastAPI exception handler。

对齐 contracts/api.md 错误码表：
| HTTP | code          | 场景                         |
|------|---------------|------------------------------|
| 400  | INVALID_INPUT | 参数校验失败                 |
| 401  | UNAUTHORIZED  | 未认证/token 失效            |
| 403  | FORBIDDEN     | 无权限/非资源 owner          |
| 404  | NOT_FOUND     | 资源不存在                   |
| 409  | CONFLICT      | 状态冲突（已在执行/终态/邮箱重复/组引用）|
| 422  | VALIDATION_ERROR | schema 校验失败           |
| 429  | RATE_LIMITED  | 配额/频率限制                |

输出统一为：{"error": {"code": "...", "message": "..."}}
"""

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = structlog.get_logger(__name__)

# 错误码 → HTTP 状态
_ERROR_MAP: dict[str, int] = {
    "INVALID_INPUT": 400,
    "UNAUTHORIZED": 401,
    "FORBIDDEN": 403,
    "NOT_FOUND": 404,
    "CONFLICT": 409,
    "VALIDATION_ERROR": 422,
    "RATE_LIMITED": 429,
    "INTERNAL_ERROR": 500,
}


class AppError(Exception):
    """业务异常：code 必须是 _ERROR_MAP 中的键。"""

    def __init__(self, code: str, message: str, *, detail: dict | None = None):
        self.code = code
        self.message = message
        self.detail = detail
        super().__init__(message)

    @property
    def status_code(self) -> int:
        return _ERROR_MAP.get(self.code, 400)


def _error_body(code: str, message: str, detail: dict | None = None) -> dict:
    body: dict = {"error": {"code": code, "message": message}}
    if detail:
        body["error"]["detail"] = detail
    return body


def register_error_handlers(app: FastAPI) -> None:
    """注册全部错误处理器。"""

    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(exc.code, exc.message, exc.detail),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(
        _: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        code = {
            400: "INVALID_INPUT",
            401: "UNAUTHORIZED",
            403: "FORBIDDEN",
            404: "NOT_FOUND",
            409: "CONFLICT",
            429: "RATE_LIMITED",
        }.get(exc.status_code, "INVALID_INPUT")
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(code, str(exc.detail)),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors = []
        for err in exc.errors():
            errors.append(
                {
                    "loc": ".".join(str(x) for x in err.get("loc", [])),
                    "msg": err.get("msg", ""),
                }
            )
        return JSONResponse(
            status_code=422,
            content=_error_body(
                "VALIDATION_ERROR", "请求参数校验失败", {"errors": errors}
            ),
        )

    @app.exception_handler(IntegrityError)
    async def integrity_error_handler(_: Request, exc: IntegrityError) -> JSONResponse:
        # 唯一约束冲突 → 409；其余数据库错误按 400 处理
        if "duplicate" in str(exc.orig).lower() or "unique" in str(exc.orig).lower():
            return JSONResponse(
                status_code=409,
                content=_error_body("CONFLICT", "数据冲突：唯一约束被违反"),
            )
        return JSONResponse(
            status_code=400,
            content=_error_body("INVALID_INPUT", "数据库操作失败"),
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        """兜底：未预期异常也返回统一 JSON 结构。

        否则 FastAPI 返回纯文本 "Internal Server Error"，前端 extractError
        拿不到 message，只能显示「请求失败，请稍后重试」——排查时完全无线索。
        详情只进日志（含堆栈），不回传给客户端（避免泄漏内部实现）。
        """
        logger.exception(
            "unhandled_error",
            path=request.url.path,
            method=request.method,
            error_type=type(exc).__name__,
        )
        return JSONResponse(
            status_code=500,
            content=_error_body("INTERNAL_ERROR", "服务器内部错误，请稍后重试"),
        )
