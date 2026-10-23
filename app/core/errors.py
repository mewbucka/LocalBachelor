"""Structured JSON error responses with correlation IDs.

Every error response has the shape:
    {"error": "<machine code>", "detail": <string or list>, "correlation_id": "<id>"}

The correlation ID is taken from the incoming X-Request-ID header when
present, otherwise generated. It is echoed in the X-Request-ID response
header and in every log line the error produces, so a reported request can
be found in the logs.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

CORRELATION_HEADER = "X-Request-ID"

_ERROR_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    422: "validation_error",
    500: "internal_error",
}


def correlation_id_for(request: Request) -> str:
    """Reuse the incoming X-Request-ID or generate one; stash on request.state."""
    cid = request.headers.get(CORRELATION_HEADER) or uuid.uuid4().hex[:16]
    request.state.correlation_id = cid
    return cid


def _response(
    request: Request, cid: str, status_code: int, code: str, detail: object
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": code, "detail": detail, "correlation_id": cid},
        headers={CORRELATION_HEADER: cid},
    )


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """404 (and any other HTTP error) → structured JSON."""
    # Dispatch is by exception class, so this handler only ever receives
    # StarletteHTTPException (or a subclass); the assert documents that.
    assert isinstance(exc, StarletteHTTPException)
    cid = correlation_id_for(request)
    code = _ERROR_CODES.get(exc.status_code, "http_error")
    detail = exc.detail if isinstance(exc.detail, str) else str(jsonable_encoder(exc.detail))
    log = logger.error if exc.status_code >= 500 else logger.info
    log(
        "http error",
        extra={
            "correlation_id": cid,
            "status_code": exc.status_code,
            "path": request.url.path,
            "method": request.method,
        },
    )
    return _response(request, cid, exc.status_code, code, detail)


async def validation_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """422 (request validation) → structured JSON with the error list."""
    # Same dispatch guarantee as http_exception_handler.
    assert isinstance(exc, RequestValidationError)
    cid = correlation_id_for(request)
    detail = jsonable_encoder(exc.errors())
    logger.info(
        "validation error",
        extra={"correlation_id": cid, "path": request.url.path, "errors": detail},
    )
    return _response(request, cid, 422, "validation_error", detail)


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Any unhandled exception → 500 JSON, exception logged (never leaked)."""
    cid = correlation_id_for(request)
    logger.exception(
        "unhandled exception",
        extra={"correlation_id": cid, "path": request.url.path, "method": request.method},
    )
    return _response(request, cid, 500, "internal_error", "Internal server error")


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
