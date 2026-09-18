from __future__ import annotations

import hmac
import os

from fastapi import Request
from fastapi.responses import JSONResponse


def configured_api_key() -> str:
    return os.getenv("TRADECLERK_API_KEY", "").strip()


def provided_api_key(request: Request) -> str:
    header = (request.headers.get("x-api-key") or "").strip()
    if header:
        return header
    auth = (request.headers.get("authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def keys_match(provided: str, expected: str) -> bool:
    left = provided.encode("utf-8")
    right = expected.encode("utf-8")
    if len(left) != len(right):
        hmac.compare_digest(right, right)
        return False
    return hmac.compare_digest(left, right)


def reject_unauthorized(request: Request) -> JSONResponse | None:
    if not request.url.path.startswith("/v1/"):
        return None
    expected = configured_api_key()
    if not expected:
        return JSONResponse({"error": "TRADECLERK_API_KEY is not configured"}, status_code=503)
    provided = provided_api_key(request)
    if not provided or not keys_match(provided, expected):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return None
