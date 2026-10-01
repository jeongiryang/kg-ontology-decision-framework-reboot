from __future__ import annotations

from importlib.resources import files
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import Response
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

import hashlib
import asyncio
from functools import lru_cache, wraps
from threading import RLock
from threading import BoundedSemaphore
from starlette.concurrency import run_in_threadpool

from .core import AnswerEngine, SANITIZED_DEPARTMENT, canonical_response_json, validate_request_safety
from .feedback import store_feedback
from .grounded_chat import GroundedChatEngine, GroundedChatResponse
from .models import AcademicAnswerRequest, AcademicChatRequest, AcademicAnswerResponse, AcademicFeedbackRequest, AcademicFeedbackResponse
from .registry import Registry, RegistryUnavailable
from .neo4j_evidence import Neo4jEvidenceReader
from .transcript_models import TranscriptExtraction, TranscriptAssessmentRequest, TranscriptAssessmentResponse, TranscriptFollowupRequest, TranscriptFollowupResponse
from .transcript_assessment import TranscriptAssessor


def _single_instance(factory):
    """lru_cache alone permits duplicate factories during concurrent misses."""
    cached = lru_cache(maxsize=1)(factory)
    lock = RLock()
    @wraps(factory)
    def get():
        with lock:
            return cached()
    def clear():
        with lock:
            cached.cache_clear()
    def info():
        with lock:
            return cached.cache_info()
    get.cache_clear = clear
    get.cache_info = info
    return get


@asynccontextmanager
async def _lifespan(_app):
    try:
        yield
    finally:
        _chat_engine.cache_clear()
        try:
            if _engine.cache_info().currsize:
                reader = _engine().evidence_reader
                if reader is not None:
                    reader.close()
        finally:
            _engine.cache_clear()

app = FastAPI(
    title="Academic Assistant",
    version="1.1.0",
    lifespan=_lifespan,
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


@app.get("/assets/semantic-ui.js", include_in_schema=False)
def semantic_script() -> Response:
    return _web_asset("semantic-ui.js", "text/javascript; charset=utf-8")


from .assistant_models import AssistantTurnRequest, AssistantTurnResponse


@app.get("/v1/academic/transcripts/examples/{identifier}.pdf")
def synthetic_pdf(identifier: str):
    from .transcript_examples import example_pdf
    try:
        _fixture, data = example_pdf(identifier)
        return Response(data, media_type="application/pdf")
    except KeyError:
        raise HTTPException(status_code=404, detail="example not found") from None
    except Exception:
        raise HTTPException(status_code=503, detail="example unavailable") from None


@app.post("/v1/academic/transcripts/examples/{identifier}")
def synthetic_transcript(identifier: str):
    from .transcript_examples import recognize_example
    if not _EXTRACTION_SLOT.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="transcript extraction busy")
    try:
        return recognize_example(_engine(), identifier)
    except KeyError:
        raise HTTPException(status_code=404, detail="example not found") from None
    except Exception:
        raise HTTPException(status_code=503, detail="synthetic PDF recognition unavailable") from None
    finally:
        _EXTRACTION_SLOT.release()


@app.post("/v1/academic/assistant", response_model=AssistantTurnResponse)
def semantic_turn(request: AssistantTurnRequest):
    from .assistant import SemanticAssistant
    try:
        engine = _engine()
        if engine.registry.catalogue is None:
            raise RegistryUnavailable()
        return SemanticAssistant(engine=engine).chat(request)
    except ValueError:
        return _invalid_response()
    except RegistryUnavailable:
        raise HTTPException(status_code=503, detail="academic registry unavailable") from None


@app.get("/assets/transcript.js", include_in_schema=False)
def transcript_script() -> Response:
    return _web_asset("transcript.js", "text/javascript; charset=utf-8")


@app.get("/assets/evidence.js", include_in_schema=False)
def evidence_script() -> Response:
    return _web_asset("evidence.js", "text/javascript; charset=utf-8")


@app.get("/assets/evidence.css", include_in_schema=False)
def evidence_styles() -> Response:
    return _web_asset("evidence.css", "text/css; charset=utf-8")


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


@_single_instance
def _engine() -> AnswerEngine:
    registry = Registry.load()
    return AnswerEngine(registry, evidence_reader=Neo4jEvidenceReader.from_env())


@_single_instance
def _chat_engine() -> GroundedChatEngine:
    return GroundedChatEngine(_engine())


@app.get("/readyz")
def readiness() -> dict[str, str]:
    try:
        engine = _engine()
        if engine.evidence_reader is not None:
            engine.evidence_reader.verify(engine.registry)
    except RegistryUnavailable:
        raise HTTPException(status_code=503, detail="academic registry unavailable") from None
    return {"status": "ready"}


@app.get("/v1/academic/runtime")
def runtime_status():
    """Separate configuration/inventory from successful inference; expose no connection secrets."""
    from .llm import LocalLLMClient
    from .grounded_generation import grounded_generation_enabled
    try:
        engine = _engine()
        if engine.evidence_reader is not None:
            engine.evidence_reader.verify(engine.registry)
    except RegistryUnavailable:
        raise HTTPException(status_code=503, detail="service unavailable") from None
    try:
        llm = LocalLLMClient.from_env()
        generation_enabled = grounded_generation_enabled()
        model_available = llm.model_available() if llm else False
    except ValueError:
        llm, model_available, generation_enabled = None, False, False
    return {"schema_version": "1.0.0", "evidence_backend": "neo4j" if engine.evidence_reader else "registry",
            "graph_verified": engine.evidence_reader is not None,
            "llm_configured": llm is not None, "llm_model_available": model_available,
            "llm_mode": "grounded_answer_generation" if generation_enabled else "topic_code_suggestions",
            "student_records_sent_to_llm": False,
            "default_dialogue_mode": "semantic_retrieval",
            "course_catalogue_count": len((engine.registry.catalogue or {}).get("courses", [])),
            "nonidentifying_questions_sent_to_llm": True}


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


@app.post("/v1/academic/chat", response_model=GroundedChatResponse)
def create_chat_answer(request: AcademicChatRequest):
    try:
        validate_request_safety(request)
        result = _chat_engine().chat(request)
    except RegistryUnavailable:
        return Response('{"detail":"service unavailable"}', status_code=503, media_type="application/json")
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


_EXTRACTION_SLOT = BoundedSemaphore(1)
_MAX_PDF_BYTES = 10 * 1024 * 1024
UPLOAD_RECEIVE_TIMEOUT_SECONDS = 30


@app.post("/v1/academic/transcripts/extract", response_model=TranscriptExtraction)
async def extract_transcript_pdf(request: Request, page_number: int | None = Query(default=None, ge=1, le=10)):
    """Raw PDF body: no filename, multipart spooling, file cache or student logs."""
    if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/pdf":
        return _invalid_response(400)
    if not _EXTRACTION_SLOT.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="extraction busy")
    try:
        data = bytearray()
        # One absolute deadline covers the whole upload, including trickled chunks.
        async with asyncio.timeout(UPLOAD_RECEIVE_TIMEOUT_SECONDS):
            async for chunk in request.stream():
                if len(data) + len(chunk) > _MAX_PDF_BYTES:
                    raise HTTPException(status_code=413, detail="file too large")
                data.extend(chunk)
        from .transcript_process import extract_isolated
        return await run_in_threadpool(extract_isolated, bytes(data), page_number=page_number)
    except ValueError:
        return _invalid_response()
    except TimeoutError:
        raise HTTPException(status_code=408, detail="upload timed out") from None
    finally:
        _EXTRACTION_SLOT.release()


@app.post("/v1/academic/transcripts/assess", response_model=TranscriptAssessmentResponse)
def assess_transcript(request: TranscriptAssessmentRequest):
    try:
        return TranscriptAssessor(_engine()).assess(request)
    except RegistryUnavailable:
        raise HTTPException(status_code=503, detail="service unavailable") from None


@app.post("/v1/academic/transcripts/chat", response_model=TranscriptFollowupResponse)
def transcript_followup(request: TranscriptFollowupRequest):
    try:
        return TranscriptAssessor(_engine()).followup(request)
    except RegistryUnavailable:
        raise HTTPException(status_code=503, detail="service unavailable") from None
    except ValueError:
        return _invalid_response()


from .evidence_pdf import create_evidence_router
app.include_router(create_evidence_router(_engine))
