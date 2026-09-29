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
        if not 0 < self.timeout_seconds <= 10:
            raise ValueError("invalid local LLM timeout")
        if not 0 < self.max_response_bytes <= 32768:
            raise ValueError("invalid local LLM response bound")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> LLMSettings | None:
        values = os.environ if environ is None else environ
        provider = values.get("ACADEMIC_LLM_PROVIDER", "disabled").strip().lower()
        if provider in {"", "disabled"}:
            return None
        try:
            timeout = float(values.get("ACADEMIC_LLM_TIMEOUT_SECONDS", "3"))
            max_bytes = int(values.get("ACADEMIC_LLM_MAX_RESPONSE_BYTES", "8192"))
        except ValueError as exc:
            raise ValueError("invalid local LLM limits") from exc
        return cls(
            provider=provider,
            base_url=values.get("ACADEMIC_LLM_BASE_URL", ""),
            model=values.get("ACADEMIC_LLM_MODEL", ""),
            api_key=values.get("ACADEMIC_LLM_API_KEY", ""),
            timeout_seconds=timeout,
            max_response_bytes=max_bytes,
        )


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        raise HTTPError(request.full_url, code, "LLM redirects are disabled", headers, fp)


class LocalLLMClient:
    def __init__(self, settings: LLMSettings) -> None:
        self.settings = settings
        # A process-wide HTTP_PROXY must not forward a student's question.
        self._opener = build_opener(ProxyHandler({}), _NoRedirects())

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> LocalLLMClient | None:
        settings = LLMSettings.from_env(environ)
        return cls(settings) if settings is not None else None

    def suggest_intent(self, signal: str, candidates: Mapping[str, str]) -> str | None:
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
        if self.settings.provider == "ollama":
            path = "/api/generate"
            payload = {
                "model": self.settings.model,
                "prompt": json.dumps(task, ensure_ascii=False, separators=(",", ":")),
                "format": "json",
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
                        "schema": {
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
                        },
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
            outer = json.loads(raw.decode("utf-8"))
            if self.settings.provider == "ollama":
                content = outer["response"]
            else:
                content = outer["choices"][0]["message"]["content"]
            parsed = json.loads(content)
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
