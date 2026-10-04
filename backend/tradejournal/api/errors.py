"""Every error leaves the API as RFC 9457 `application/problem+json` (doc 07 section 2)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import ApiConfig
from .ports import AdminRefusal
from .requestlog import REQUEST_ID_HEADER
from .schemas import PROBLEM_MEDIA_TYPE, FieldError, Problem
from .security import security_headers


def problem_response(
    status: int,
    detail: str | None = None,
    *,
    errors: Sequence[FieldError] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    problem = Problem(
        title=HTTPStatus(status).phrase, status=status, detail=detail, errors=list(errors) if errors else None
    )
    return JSONResponse(
        problem.model_dump(mode="json"), status_code=status, media_type=PROBLEM_MEDIA_TYPE, headers=dict(headers or {})
    )


def install_error_handlers(app: FastAPI, config: ApiConfig) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else None
        if detail == HTTPStatus(exc.status_code).phrase:
            detail = None  # Starlette fills in the phrase for plain 404s and 405s; it only repeats the title
        return problem_response(exc.status_code, detail, headers=exc.headers)

    @app.exception_handler(AdminRefusal)
    async def admin_refusal(request: Request, exc: AdminRefusal) -> JSONResponse:
        return problem_response(exc.status, exc.detail)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # `loc` starts with where the value came from ("body", "query", ...); the client only needs the field path.
        # The offending input is deliberately not echoed back.
        errors = [
            FieldError(field=".".join(str(part) for part in err["loc"][1:]), message=err["msg"]) for err in exc.errors()
        ]
        return problem_response(422, "The request is not valid.", errors=errors)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        # Runs outside the middleware stack (Starlette's outermost layer), so the security headers are added here.
        # Nothing about the failure reaches the client; the server log has its kind and where it happened (api/logs.py).
        # The request id goes back so a person can quote it: the request log set it before the failure.
        headers = security_headers(request.url.path, config)
        request_id = request.scope.get("state", {}).get("request_id")
        if request_id:
            headers[REQUEST_ID_HEADER] = request_id
        return problem_response(500, headers=headers)
