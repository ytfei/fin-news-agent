"""统一错误处理（RFC 9457 Problem Details）。"""
from __future__ import annotations

import uuid

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from fin_news.core.logging import get_logger

logger = get_logger("api.errors")


class AppError(Exception):
    def __init__(self, status_code: int, detail: str, title: str = "错误") -> None:
        self.status_code = status_code
        self.detail = detail
        self.title = title
        super().__init__(detail)


class NotFoundError(AppError):
    def __init__(self, detail: str = "资源不存在") -> None:
        super().__init__(status.HTTP_404_NOT_FOUND, detail, "资源不存在")


class BadRequestError(AppError):
    def __init__(self, detail: str = "请求参数错误") -> None:
        super().__init__(status.HTTP_400_BAD_REQUEST, detail, "参数错误")


class ServiceUnavailableError(AppError):
    def __init__(self, detail: str = "依赖服务不可用") -> None:
        super().__init__(status.HTTP_503_SERVICE_UNAVAILABLE, detail, "服务不可用")


def _problem(request: Request, status_code: int, detail: str, title: str, trace_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        media_type="application/problem+json",
        content={
            "type": f"https://fin-news.local/errors/{status_code}",
            "title": title,
            "status": status_code,
            "detail": detail,
            "instance": str(request.url.path),
            "trace_id": trace_id,
        },
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        trace_id = uuid.uuid4().hex[:16]
        logger.warning("业务异常", detail=exc.detail, status=exc.status_code, trace_id=trace_id)
        return _problem(request, exc.status_code, exc.detail, exc.title, trace_id)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        trace_id = uuid.uuid4().hex[:16]
        detail = "; ".join(f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()[:5])
        return _problem(request, status.HTTP_400_BAD_REQUEST, detail, "参数错误", trace_id)

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        trace_id = uuid.uuid4().hex[:16]
        logger.exception("未处理异常", error=str(exc), trace_id=trace_id)
        return _problem(request, status.HTTP_500_INTERNAL_SERVER_ERROR, "服务器内部错误", "内部错误", trace_id)
