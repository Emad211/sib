"""One error envelope for the JSON API and one Persian page for browsers.

`{"error": {"code": ..., "message": ..., "details": ...}}` is the whole contract; codes
are stable for the client, messages are for people. Service refusals are translated to
`(status, code, message)` here, so a route never turns a policy refusal into a 500.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from shift.api.templating import templates
from shift.domain.pin_policy import PinPolicyError
from shift.services.auth import (
    AccountLocked,
    AuthError,
    DeviceNotEnrolled,
    DeviceRevoked,
    InvalidCredentials,
    SessionInvalid,
    StaffInactive,
    StaffInputError,
    StaffNotFound,
)
from shift.services.devices import DeviceError, DeviceNotFound, EnrollmentRejected, UnknownStaff

log = logging.getLogger(__name__)

LOGIN_PATH = "/login"
_UNEXPECTED = "خطای داخلی رخ داد؛ دوباره تلاش کنید."
_BAD_CREDENTIALS = "موبایل یا رمز نامعتبر است."
# Starlette's own refusals (unknown path, wrong verb, ...) carry the English reason phrase; staff get Persian.
_ENGLISH_DETAILS = frozenset({"Not Found", "Method Not Allowed", "Forbidden", "Bad Request", "invalid Content-Length"})
_HTTP_MESSAGES = {
    400: "درخواست نامعتبر است.",
    403: "اجازهٔ این کار را ندارید.",
    404: "این صفحه پیدا نشد.",
    405: "این درخواست در این صفحه مجاز نیست.",
    413: "حجم درخواست بیش از حد مجاز است.",
}


class ApiError(Exception):
    """Base refusal; `status` and `code` are what the client acts on."""

    status = 500
    code = "internal_error"

    def __init__(self, message: str = "", *, code: str | None = None) -> None:
        super().__init__(message or self.code)
        self.message = message
        self.error_code = code or self.code


class BadRequest(ApiError):
    status = 400
    code = "invalid_request"


class Unauthorized(ApiError):
    status = 401
    code = "not_authenticated"


class Forbidden(ApiError):
    status = 403
    code = "forbidden"


class NotFound(ApiError):
    status = 404
    code = "not_found"


class Conflict(ApiError):
    status = 409
    code = "conflict"


class TooManyAttempts(ApiError):
    status = 429
    code = "too_many_attempts"


class CsrfRejected(ApiError):
    status = 403
    code = "csrf_failed"


class LoginRequired(Exception):
    """A page needs a session: send the browser to the login screen."""

    def __init__(self, next_path: str) -> None:
        super().__init__("login required")
        self.next_path = next_path


def _wants_html(request: Request) -> bool:
    return not request.url.path.startswith("/api/") and "text/html" in request.headers.get("accept", "")


def _error_response(
    request: Request,
    status: int,
    code: str,
    message: str,
    details: dict[str, object] | None = None,
) -> JSONResponse | HTMLResponse:
    if _wants_html(request):
        page = templates.get_template("error.html")
        return HTMLResponse(
            page.render(status=status, code=code, message=message),
            status_code=status,
        )
    body: dict[str, object] = {"code": code, "message": message}
    if details:
        body["details"] = details
    return JSONResponse({"error": body}, status_code=status)


def describe_refusal(exc: Exception) -> tuple[int, str, str, dict[str, object] | None]:
    """Translate a service refusal into `(status, code, message, details)`."""
    if isinstance(exc, (InvalidCredentials, StaffInactive)):
        # One envelope for every refusal about the person: a different status, code or message
        # for a deactivated account would tell an enrolled phone which mobiles are staff.
        # The reason is in the audit log, where only the manager can read it.
        return 401, "invalid_credentials", _BAD_CREDENTIALS, None
    if isinstance(exc, AccountLocked):
        return 423, "account_locked", "حساب پس از پنج تلاش ناموفق قفل شده است؛ مدیر باید آن را باز کند.", None
    if isinstance(exc, DeviceNotEnrolled):
        return 403, "device_not_enrolled", "این گوشی برای پرسنل ثبت نشده است.", None
    if isinstance(exc, DeviceRevoked):
        return 403, "device_revoked", "دسترسی این گوشی لغو شده است.", None
    if isinstance(exc, SessionInvalid):
        return 401, "not_authenticated", "نشست معتبر نیست؛ دوباره وارد شوید.", None
    if isinstance(exc, StaffInputError):
        return (409 if exc.conflict else 400), "staff_invalid", exc.message_fa, None
    if isinstance(exc, (StaffNotFound, UnknownStaff)):
        return 404, "staff_not_found", "کارمند پیدا نشد.", None
    if isinstance(exc, EnrollmentRejected):
        return (*_enrollment_refusal(exc), None)
    if isinstance(exc, DeviceNotFound):
        return 404, "device_not_found", "دستگاه پیدا نشد.", None
    if isinstance(exc, PinPolicyError):
        return 400, "pin_rejected", "رمز انتخاب‌شده قابل حدس است؛ رمز دیگری انتخاب کنید.", {"rule": exc.problem.value}
    if isinstance(exc, DeviceError):
        return 400, "device_invalid", "اطلاعات دستگاه نامعتبر است.", None
    # Unmapped refusal: fail closed, never let it look like a success or a 200.
    return 500, "internal_error", _UNEXPECTED, None


def _enrollment_refusal(exc: EnrollmentRejected) -> tuple[int, str, str]:
    if exc.reason == "expired":
        return 410, "enrollment_token_expired", "این کد ثبت دستگاه منقضی شده است؛ کد تازه بگیرید."
    if exc.reason == "used":
        return 409, "enrollment_token_used", "این کد قبلاً استفاده شده است."
    if exc.reason == "staff_inactive":
        return 403, "staff_inactive", "این حساب غیرفعال است."
    return 404, "enrollment_token_unknown", "کد ثبت دستگاه معتبر نیست."


def install_error_handlers(app: FastAPI) -> None:
    """Register every handler; call once, before the app serves."""

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse | HTMLResponse:
        return _error_response(request, exc.status, exc.error_code, exc.message)

    @app.exception_handler(LoginRequired)
    async def _login_required(request: Request, exc: LoginRequired) -> RedirectResponse:
        return RedirectResponse(f"{LOGIN_PATH}?next={exc.next_path}", status_code=303)

    async def _refusal(request: Request, exc: Exception) -> JSONResponse | HTMLResponse:
        status, code, message, details = describe_refusal(exc)
        return _error_response(request, status, code, message, details)

    for refusal in (AuthError, DeviceError, PinPolicyError):
        app.add_exception_handler(refusal, _refusal)

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse | HTMLResponse:
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return _error_response(request, 500, "internal_error", _UNEXPECTED)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse | HTMLResponse:
        code = "not_found" if exc.status_code == 404 else "http_error"
        detail = str(exc.detail)
        if exc.status_code in _HTTP_MESSAGES and detail in _ENGLISH_DETAILS:
            detail = _HTTP_MESSAGES[exc.status_code]
        return _error_response(request, exc.status_code, code, detail)

    @app.exception_handler(RequestValidationError)
    async def _invalid(request: Request, exc: RequestValidationError) -> JSONResponse | HTMLResponse:
        return _error_response(request, 422, "invalid_request", "درخواست نامعتبر است.")
