from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class DomainError(Exception):
    status_code = 400

    def __init__(self, code: str, message: str | None = None):
        super().__init__(code)
        self.code = code
        self.message = message


class BadRequest(DomainError):
    status_code = 400


class Unauthorized(DomainError):
    status_code = 401


class Forbidden(DomainError):
    status_code = 403


class NotFound(DomainError):
    status_code = 404


class Conflict(DomainError):
    status_code = 409


class TooManyRequests(DomainError):
    status_code = 429


class ServiceUnavailable(DomainError):
    status_code = 503


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain_error(_: Request, exc: DomainError) -> JSONResponse:
        body = {"detail": exc.code}
        if exc.message:
            body["message"] = exc.message
        headers = {"Retry-After": "60"} if exc.status_code == 429 else None
        return JSONResponse(status_code=exc.status_code, content=body, headers=headers)


    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's default answer echoes the offending INPUT, which for a login form is a password or a code.
        fields = [".".join(str(p) for p in e.get("loc", ())[1:]) for e in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": "validation_error", "fields": fields})
