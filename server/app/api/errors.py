from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .trace import get_trace_id


def error_payload(
    code: str,
    message: str,
    details: object | None = None,
    user_action: str | None = None,
    retryable: bool = False,
) -> dict[str, object]:
    return {
        "error": {
            "code": code,
            "message": message,
            "user_action": user_action,
            "retryable": retryable,
            "details": details,
        },
        "trace_id": get_trace_id(),
    }


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def handle_http_exception(_request: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
        code = str(detail.get("code", "HTTP_ERROR"))
        message = str(detail.get("message", "请求失败"))
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(
                code,
                message,
                detail.get("details"),
                detail.get("user_action"),
                bool(detail.get("retryable", False)),
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error_payload(
                "VALIDATION_ERROR",
                "请求参数校验失败",
                jsonable_encoder(exc.errors(), custom_encoder={ValueError: str}),
                "检查输入字段后重试",
            ),
        )
