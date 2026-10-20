"""Contract error envelope (docs/contracts/domain-api.md §6).

Every API error — handled or not — leaves the process in this shape.
`message` is safe to display; internals stay in server logs only.
"""

import logging
import traceback
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)


def error_body(code: str, message: str, trace_id: str, details: Any = None) -> dict:
    body: dict = {"error": {"code": code, "message": message, "trace_id": trace_id}}
    if details is not None:
        body["error"]["details"] = details
    return body


def _trace_id(request: Request) -> str:
    return getattr(request.state, "trace_id", None) or uuid.uuid4().hex[:12]


_STATUS_CODES = {404: "not_found", 401: "unauthorized", 403: "forbidden", 429: "rate_limited"}


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict) and isinstance(detail.get("code"), str):
        # Explicit code from the raiser (e.g. contract_mismatch). Message only.
        code, message = detail["code"], str(detail.get("message", ""))
    else:
        code, message = _STATUS_CODES.get(exc.status_code, "http_error"), str(detail)
    return JSONResponse(
        status_code=exc.status_code,
        content=error_body(code, message, _trace_id(request)),
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content=error_body(
            "validation_error",
            "Request failed validation.",
            _trace_id(request),
            # Installed pydantic has no `include_url` kwarg; plain errors()
            # carries no URLs there. Stage 7.0 query validation exercises
            # this path for the first time (pinned by test_reads.py).
            details=exc.errors(),
        ),
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    trace_id = _trace_id(request)
    # Full traceback goes to server logs only — never to the client.
    log.error("unhandled error trace_id=%s\n%s", trace_id, "".join(traceback.format_exception(exc)))
    return JSONResponse(
        status_code=500,
        content=error_body("internal", "Unexpected server error.", trace_id),
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
