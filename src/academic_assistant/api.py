from __future__ import annotations

from importlib.resources import files

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

import hashlib
from functools import lru_cache

from .core import AnswerEngine, SANITIZED_DEPARTMENT, canonical_response_json, validate_request_safety
from .feedback import store_feedback
from .models import AcademicAnswerRequest, AcademicAnswerResponse, AcademicFeedbackRequest, AcademicFeedbackResponse
from .registry import Registry, RegistryUnavailable

app = FastAPI(
    title="Academic Assistant",
    version="1.1.0",
)

_SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'none'; base-uri 'none'; connect-src 'self'; "
        "form-action 'self'; frame-ancestors 'none'; img-src 'self'; "
        "script-src 'self'; style-src 'self'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}
_DOCS_CSP = (
    "default-src 'none'; base-uri 'none'; connect-src 'self'; frame-ancestors 'none'; "
    "font-src https://cdn.jsdelivr.net https://fonts.gstatic.com; "
    "img-src 'self' data: https://fastapi.tiangolo.com; "
    "script-src 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com"
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    for name, value in _SECURITY_HEADERS.items():
        response.headers[name] = value
    if request.url.path in {"/docs", "/redoc", "/docs/oauth2-redirect"}:
        response.headers["Content-Security-Policy"] = _DOCS_CSP
    return response


def _web_asset(name: str, media_type: str) -> Response:
    content = files("academic_assistant").joinpath("web", name).read_bytes()
    return Response(content, media_type=media_type)


@app.get("/", include_in_schema=False)
def web_prototype() -> Response:
    return _web_asset("index.html", "text/html; charset=utf-8")


@app.get("/assets/app.css", include_in_schema=False)
def web_styles() -> Response:
    return _web_asset("app.css", "text/css; charset=utf-8")


@app.get("/assets/app.js", include_in_schema=False)
def web_script() -> Response:
    return _web_asset("app.js", "text/javascript; charset=utf-8")


def _invalid_response(status_code: int = 422) -> Response:
    return Response('{"detail":"invalid request"}', status_code=status_code, media_type="application/json")


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(_request, _exc: RequestValidationError) -> Response:
    return _invalid_response()


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(_request, exc: StarletteHTTPException) -> Response:
    if exc.status_code in {400, 422}:
        return _invalid_response(exc.status_code)
    return Response('{"detail":"service unavailable"}', status_code=exc.status_code, media_type="application/json")


@lru_cache(maxsize=1)
def _engine() -> AnswerEngine:
    return AnswerEngine(Registry.load())


@app.get("/readyz")
def readiness() -> dict[str, str]:
    try:
        _engine()
    except RegistryUnavailable:
        raise HTTPException(status_code=503, detail="academic registry unavailable") from None
    return {"status": "ready"}


@app.post("/v1/academic/answers", response_model=AcademicAnswerResponse)
def create_answer(request: AcademicAnswerRequest):
    try:
        validate_request_safety(request)
        result = _engine().answer(request)
    except RegistryUnavailable:
        packet_id = "academic-" + hashlib.sha256(b"registry-unavailable").hexdigest()[:32]
        scope = {"admission_year": request.admission_year, "matched_curriculum_year": request.matched_curriculum_year, "department": SANITIZED_DEPARTMENT}
        result = AnswerEngine._unsupported(packet_id, scope, "insufficient_evidence", "학사 근거 저장소를 확인할 수 없어 답변할 수 없습니다.", "review")
        return Response(canonical_response_json(result), status_code=503, media_type="application/json")
    except ValueError:
        return _invalid_response()
    return Response(canonical_response_json(result), status_code=200, media_type="application/json")


@app.post("/v1/academic/feedback", response_model=AcademicFeedbackResponse, status_code=201)
def create_feedback(request: AcademicFeedbackRequest):
    try:
        answer_request = AcademicAnswerRequest(
            question=request.question,
            admission_year=2026,
            matched_curriculum_year=2026,
            department="컴퓨터공학과",
            earned_credits=request.earned_credits,
        )
        validate_request_safety(answer_request)
        answer = _engine().answer(answer_request)
        if answer.packet_id != request.packet_id or answer.status != request.status or answer.status == "supported":
            raise ValueError("feedback does not match a current unsupported answer")
        result = store_feedback(request)
    except RegistryUnavailable:
        return Response('{"detail":"service unavailable"}', status_code=503, media_type="application/json")
    except ValueError:
        return _invalid_response()
    except OSError:
        return Response('{"detail":"service unavailable"}', status_code=503, media_type="application/json")
    return result
