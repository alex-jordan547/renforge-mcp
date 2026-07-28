from __future__ import annotations

from typing import Any

from starlette.responses import JSONResponse


def error_response(
    *,
    code: str,
    error: str,
    status_code: int,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    payload = {
        "ok": False,
        "error_code": code,
        "details": details or {},
        "error": error,
    }
    return JSONResponse(payload, status_code=status_code)


def with_error_code(
    *,
    code: str,
    error: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "error_code": code,
        "details": details or {},
        "error": error,
    }


__all__ = ["error_response", "with_error_code"]
