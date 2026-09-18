from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

import hashlib
from functools import lru_cache

from .core import AnswerEngine, SANITIZED_DEPARTMENT, canonical_response_json, validate_request_safety
from .models import AcademicAnswerRequest, AcademicAnswerResponse
from .registry import Registry, RegistryUnavailable

app = FastAPI(title="Academic Assistant", version="1.0.0")


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
