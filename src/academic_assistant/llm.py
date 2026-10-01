"""Optional, loopback-only LLM intent suggestion.

The model is never an academic source and cannot author an answer. Its sole
output is an untrusted catalog intent identifier, which callers must check
against the approved registry before showing a static clarification prompt.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import math
import hashlib
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


ALLOWED_INTENT_SIGNALS = frozenset({
    "counseling", "course", "credits", "curriculum", "general", "graduation",
    "leave", "major", "retake", "term", "thesis", "transfer",
})


class IntentSuggester(Protocol):
    def suggest_intent(self, signal: str, candidates: Mapping[str, str]) -> str | None: ...


class LLMUnavailable(RuntimeError):
    """The optional provider did not produce a usable response."""


class LLMInvalidResponse(ValueError):
    """The provider returned output outside the one-ID contract."""


@dataclass(frozen=True)
class LLMSettings:
    provider: str
    base_url: str
    model: str
    api_key: str = ""
    timeout_seconds: float = 3.0
    max_response_bytes: int = 8192
    min_interval_seconds: float = 5.0
    failure_cooldown_seconds: float = 60.0

    def __post_init__(self) -> None:
        if self.provider not in {"ollama", "openai", "lmstudio"}:
            raise ValueError("unsupported local LLM provider")
        parsed = urlsplit(self.base_url)
        try:
            address = ipaddress.ip_address(parsed.hostname or "")
        except ValueError as exc:
            raise ValueError("LLM endpoint must use a numeric loopback address") from exc
        if (
            parsed.scheme not in {"http", "https"}
            or not address.is_loopback
            or not parsed.port
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("LLM endpoint must be a loopback URL with explicit port and no path")
        if not self.model or len(self.model) > 200 or any(ord(char) < 33 for char in self.model):
            raise ValueError("invalid local LLM model")
        if any(ord(char) < 32 or ord(char) == 127 for char in self.api_key):
            raise ValueError("invalid local LLM API key")
        if not math.isfinite(self.timeout_seconds) or not 0 < self.timeout_seconds <= 30:
            raise ValueError("invalid local LLM timeout")
        if not 0 < self.max_response_bytes <= 32768:
            raise ValueError("invalid local LLM response bound")
        if (not math.isfinite(self.min_interval_seconds) or not 2 <= self.min_interval_seconds <= 600
                or not math.isfinite(self.failure_cooldown_seconds) or not 60 <= self.failure_cooldown_seconds <= 3600):
            raise ValueError("invalid local LLM budget")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> LLMSettings | None:
        values = os.environ if environ is None else environ
        provider = values.get("ACADEMIC_LLM_PROVIDER", "disabled").strip().lower()
        if provider in {"", "disabled"}:
            return None
        try:
            timeout = float(values.get("ACADEMIC_LLM_TIMEOUT_SECONDS", "3"))
            max_bytes = int(values.get("ACADEMIC_LLM_MAX_RESPONSE_BYTES", "8192"))
            interval = float(values.get("ACADEMIC_LLM_MIN_INTERVAL_SECONDS", "5"))
            cooldown = float(values.get("ACADEMIC_LLM_FAILURE_COOLDOWN_SECONDS", "60"))
        except ValueError as exc:
            raise ValueError("invalid local LLM limits") from exc
        return cls(
            provider=provider,
            base_url=values.get("ACADEMIC_LLM_BASE_URL", ""),
            model=values.get("ACADEMIC_LLM_MODEL", ""),
            api_key=values.get("ACADEMIC_LLM_API_KEY", ""),
            timeout_seconds=timeout,
            max_response_bytes=max_bytes,
            min_interval_seconds=interval,
            failure_cooldown_seconds=cooldown,
        )


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        raise HTTPError(request.full_url, code, "LLM redirects are disabled", headers, fp)


def _unique_json_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise LLMInvalidResponse("duplicate local LLM JSON property")
        value[key] = item
    return value


class _RequestBudget:
    """One inference per process, no queue; bounded five-minute topic-ID cache."""
    def __init__(self):
        self.lock = threading.Lock()
        self.active = False
        self.next_allowed = 0.0
        self.cache = OrderedDict()


_budget = _RequestBudget()


class LocalLLMClient:
    def __init__(self, settings: LLMSettings) -> None:
        self.settings = settings
        # A process-wide HTTP_PROXY must not forward a student's question.
        self._opener = build_opener(ProxyHandler({}), _NoRedirects())

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> LocalLLMClient | None:
        settings = LLMSettings.from_env(environ)
        return cls(settings) if settings is not None else None

    def model_available(self) -> bool:
        """Read-only model inventory. Never loads a model or runs inference."""
        path = "/api/tags" if self.settings.provider == "ollama" else "/v1/models"
        headers = {"Accept": "application/json"}
        if self.settings.api_key:
            headers["Authorization"] = "Bearer " + self.settings.api_key
        request = Request(self.settings.base_url.rstrip("/") + path, headers=headers)
        try:
            with self._opener.open(request, timeout=min(self.settings.timeout_seconds, 3)) as response:
                raw = response.read(32769)
            if len(raw) > 32768:
                return False
            payload = json.loads(raw)
            if self.settings.provider == "ollama":
                models = payload.get("models", [])
                return any(isinstance(item, dict) and item.get("name") == self.settings.model for item in models)
            models = payload.get("data", [])
            return any(isinstance(item, dict) and item.get("id") == self.settings.model for item in models)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, TypeError, AttributeError):
            return False

    def suggest_intent(self, signal: str, candidates: Mapping[str, str]) -> str | None:
        parts = signal.split("|")
        if (not signal or len(signal) > 200 or len(parts) > 12
                or parts != sorted(set(parts)) or any(part not in ALLOWED_INTENT_SIGNALS for part in parts)
                or not candidates or len(candidates) > 100
                or any(re.fullmatch(r"[a-z][a-z0-9.-]{0,199}", key) is None for key in candidates)):
            raise ValueError("local LLM request outside bounds")
        key = (self.settings.provider, self.settings.base_url, self.settings.model,
            hashlib.sha256(self.settings.api_key.encode()).digest(), self.settings.timeout_seconds,
            self.settings.max_response_bytes, signal, tuple(sorted(candidates)))
        with _budget.lock:
            now = time.monotonic()
            cached = _budget.cache.get(key)
            if cached is not None and now - cached[0] < 300:
                _budget.cache.move_to_end(key)
                return cached[1]
            if _budget.active or now < _budget.next_allowed:
                return None
            _budget.active = True
        succeeded = False
        try:
            result = self._request_intent(signal, candidates)
            succeeded = True
            with _budget.lock:
                _budget.cache[key] = (time.monotonic(), result)
                _budget.cache.move_to_end(key)
                while len(_budget.cache) > 32:
                    _budget.cache.popitem(last=False)
            return result
        finally:
            with _budget.lock:
                _budget.active = False
                delay = self.settings.min_interval_seconds if succeeded else self.settings.failure_cooldown_seconds
                _budget.next_allowed = time.monotonic() + delay

    def _request_intent(self, signal: str, candidates: Mapping[str, str]) -> str | None:
        signal_parts = signal.split("|")
        if (
            not signal or len(signal) > 200
            or len(signal_parts) > 12
            or signal_parts != sorted(set(signal_parts))
            or any(part not in ALLOWED_INTENT_SIGNALS for part in signal_parts)
            or not candidates or len(candidates) > 100
            or any(re.fullmatch(r"[a-z][a-z0-9.-]{0,199}", key) is None for key in candidates)
        ):
            raise ValueError("local LLM request outside bounds")
        # The provider sees approved IDs, never the caller's label text.
        catalog = sorted(candidates)
        task = {
            "instruction": "Select exactly one relevant intent_id from the catalog, or null. Return only JSON with the intent_id key. These are fixed non-identifying topic codes, not a student's question. Do not answer an academic question.",
            "academic_signals": signal_parts,
            "catalog": catalog,
        }
        output_schema = {
            "type": "object",
            "properties": {
                "intent_id": {
                    "anyOf": [
                        {"type": "string", "enum": catalog},
                        {"type": "null"},
                    ]
                }
            },
            "required": ["intent_id"],
            "additionalProperties": False,
        }
        if self.settings.provider == "ollama":
            path = "/api/generate"
            payload = {
                "model": self.settings.model,
                "prompt": json.dumps(task, ensure_ascii=False, separators=(",", ":")),
                # Official /api/generate accepts a JSON Schema in format and
                # think:false requests no thinking output. Unsupported models
                # fail closed; never retry with an unconstrained prompt.
                "format": output_schema,
                "think": False,
                "stream": False,
                "options": {"temperature": 0, "num_predict": 64},
            }
        else:
            path = "/v1/chat/completions"
            payload = {
                "model": self.settings.model,
                "messages": [
                    {"role": "system", "content": "Return only a JSON object with one key, intent_id. It must be a listed ID or null. Never answer the question."},
                    {"role": "user", "content": json.dumps(task, ensure_ascii=False, separators=(",", ":"))},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0,
                "max_tokens": 64,
            }
            if self.settings.provider == "lmstudio":
                # LM Studio rejects the generic json_object mode. Its strict
                # schema also confines the response to approved intent IDs.
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "academic_intent",
                        "strict": True,
                        "schema": output_schema,
                    },
                }
                payload["reasoning_effort"] = "none"
        endpoint = self.settings.base_url.rstrip("/") + path
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.settings.api_key:
            headers["Authorization"] = "Bearer " + self.settings.api_key
        request = Request(endpoint, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers=headers, method="POST")
        try:
            with self._opener.open(request, timeout=self.settings.timeout_seconds) as response:
                raw = response.read(self.settings.max_response_bytes + 1)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise LLMUnavailable("local LLM unavailable") from exc
        if len(raw) > self.settings.max_response_bytes:
            raise LLMInvalidResponse("local LLM response too large")
        try:
            outer = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_json_object)
            if self.settings.provider == "ollama":
                content = outer["response"]
            else:
                content = outer["choices"][0]["message"]["content"]
            parsed = json.loads(content, object_pairs_hook=_unique_json_object)
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise LLMInvalidResponse("invalid local LLM JSON") from exc
        if not isinstance(parsed, dict) or set(parsed) != {"intent_id"}:
            raise LLMInvalidResponse("invalid local LLM intent contract")
        intent_id = parsed["intent_id"]
        if intent_id is None:
            return None
        if not isinstance(intent_id, str) or intent_id not in candidates:
            raise LLMInvalidResponse("local LLM intent outside catalog")
        return intent_id
