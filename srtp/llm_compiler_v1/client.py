"""FreeFlow LLM client wrapper with strict JSON extraction."""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .env import load_compiler_env, provider_api_keys

# FreeFlow's BaseProvider uses httpx.Client(timeout=30.0). Large IR JSON
# proposals often need longer than 30s on free-tier Gemini.
DEFAULT_HTTP_TIMEOUT_S = 120.0
# Large four-IR proposals need headroom; pair with raised HTTP timeout.
DEFAULT_MAX_TOKENS = 8192
DEFAULT_CHAT_RETRIES = 2
_TRANSIENT_MARKERS = (
    "disconnected", "timed out", "timeout", "connection reset",
    "remote protocol", "server disconnected", "temporarily unavailable",
)


def _agent_dbg(hypothesis_id: str, location: str, message: str, data: Optional[Dict[str, Any]] = None) -> None:
    # #region agent log
    try:
        payload = {
            "sessionId": "0f1247",
            "runId": "anchor-key-rotate",
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data or {},
            "timestamp": int(time.time() * 1000),
        }
        with (Path(__file__).resolve().parents[2] / "debug-0f1247.log").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass
    # #endregion


def _provider_key_state(client: Any) -> List[Dict[str, Any]]:
    states: List[Dict[str, Any]] = []
    for provider in getattr(client, "providers", []) or []:
        keys = getattr(provider, "api_keys", None) or []
        states.append({
            "name": str(getattr(provider, "name", "unknown")),
            "n_keys": len(keys),
            "index": getattr(provider, "current_key_index", None),
        })
    return states


def _is_transient_provider_error(error: BaseException) -> bool:
    text = str(error).lower()
    return any(marker in text for marker in _TRANSIENT_MARKERS)


def _resolve_chat_retries() -> int:
    raw = os.environ.get("CUBEENGINE_LLM_CHAT_RETRIES")
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    return DEFAULT_CHAT_RETRIES



class LLMClientError(RuntimeError):
    """Raised when the model transport or JSON payload fails closed."""


@dataclass(frozen=True)
class LLMChatResult:
    content: str
    provider: str
    model: str
    parsed: Mapping[str, Any]


def _resolve_timeout_s() -> float:
    raw = os.environ.get("CUBEENGINE_LLM_TIMEOUT_S") or os.environ.get("FREEFLOW_HTTP_TIMEOUT_S")
    if raw:
        try:
            return max(5.0, float(raw))
        except ValueError:
            pass
    return DEFAULT_HTTP_TIMEOUT_S


def _resolve_max_tokens(explicit: Optional[int]) -> int:
    if explicit is not None:
        return int(explicit)
    raw = os.environ.get("CUBEENGINE_LLM_MAX_TOKENS")
    if raw:
        try:
            return max(256, int(raw))
        except ValueError:
            pass
    return DEFAULT_MAX_TOKENS


def _apply_provider_timeouts(client: Any, timeout_s: float) -> None:
    """Raise FreeFlow provider httpx timeouts without changing provider order."""

    import httpx

    for provider in getattr(client, "providers", []) or []:
        for attr in ("client", "stream_client"):
            old = getattr(provider, attr, None)
            if old is None:
                continue
            try:
                old.close()
            except Exception:
                pass
            setattr(provider, attr, httpx.Client(timeout=timeout_s))


class FreeFlowLLMClient:
    """Thin adapter around freeflow_llm.FreeFlowClient.

    ``chat_json`` always requests a single JSON object and fails closed when
    the response cannot be parsed. Tests may inject a callable ``chat_fn``.
    """

    def __init__(
        self,
        *,
        temperature: float = 0.1,
        max_tokens: Optional[int] = None,
        model: Optional[str] = None,
        chat_fn: Optional[Any] = None,
        timeout_s: Optional[float] = None,
    ) -> None:
        self.temperature = temperature
        self.max_tokens = _resolve_max_tokens(max_tokens)
        self.model = model
        self.timeout_s = float(timeout_s) if timeout_s is not None else _resolve_timeout_s()
        self._chat_fn = chat_fn
        self._client = None

    def __enter__(self) -> "FreeFlowLLMClient":
        if self._chat_fn is None:
            load_compiler_env(override=True)
            try:
                from freeflow_llm import FreeFlowClient
                from freeflow_llm import config as ff_config
                from freeflow_llm.providers import GeminiProvider, GroqProvider
            except ImportError as error:
                raise LLMClientError(
                    "freeflow-llm is not installed; run: pip install freeflow-llm"
                ) from error
            # FreeFlow ships gemini-2.5-flash; Gemini API now rejects that for new users.
            # Patch per-provider defaults only — do not pass a global model kwarg (breaks Groq).
            gemini_model = (
                os.environ.get("CUBEENGINE_LLM_GEMINI_MODEL")
                or os.environ.get("FREEFLOW_GEMINI_MODEL")
                or "gemini-3.6-flash"
            )
            defaults = getattr(ff_config, "DEFAULT_MODELS", None)
            if isinstance(defaults, dict):
                defaults["gemini"] = gemini_model
            # Pass parsed key lists explicitly so FreeFlow rotates on 429.
            # Relying on FreeFlowClient() alone can keep a stale single-key env.
            providers = []
            groq_keys = provider_api_keys("groq")
            gemini_keys = provider_api_keys("gemini")
            if groq_keys:
                providers.append(GroqProvider(api_key=groq_keys))
            if gemini_keys:
                providers.append(GeminiProvider(api_key=gemini_keys))
            if not providers:
                raise LLMClientError(
                    "No LLM API keys found. Set GEMINI_API_KEY and/or GROQ_API_KEY "
                    "in the repo-root .env (JSON array supported)."
                )
            self._client = FreeFlowClient(providers=providers)
            _apply_provider_timeouts(self._client, self.timeout_s)
            self._client.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._client is not None:
            self._client.__exit__(exc_type, exc, tb)
            self._client = None

    def chat_json(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMChatResult:
        resolved_max_tokens = self.max_tokens if max_tokens is None else int(max_tokens)
        kwargs: Dict[str, Any] = {
            "messages": [dict(item) for item in messages],
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": resolved_max_tokens,
        }
        if self.model:
            kwargs["model"] = self.model

        finish_reason: Optional[str] = None
        if self._chat_fn is not None:
            response = self._chat_fn(**kwargs)
            content = _response_content(response)
            provider = str(getattr(response, "provider", "mock") or "mock")
            model = str(getattr(response, "model", self.model or "mock") or "mock")
            finish_reason = _response_finish_reason(response)
        else:
            if self._client is None:
                raise LLMClientError("FreeFlowLLMClient must be used as a context manager")
            retries = _resolve_chat_retries()
            last_error: Optional[Exception] = None
            response = None
            for attempt in range(retries + 1):
                # #region agent log
                _agent_dbg("C", "client.py:chat_json", "provider key state before chat", {
                    "attempt": attempt + 1,
                    "retries": retries,
                    "providers": _provider_key_state(self._client),
                })
                # #endregion
                try:
                    response = self._client.chat(**kwargs)
                    last_error = None
                    # #region agent log
                    _agent_dbg("C", "client.py:chat_json", "provider key state after chat ok", {
                        "attempt": attempt + 1,
                        "providers": _provider_key_state(self._client),
                    })
                    # #endregion
                    break
                except Exception as error:  # noqa: BLE001 - provider surface varies
                    last_error = error
                    transient = _is_transient_provider_error(error)
                    # #region agent log
                    _agent_dbg("D", "client.py:chat_json", "provider chat error", {
                        "attempt": attempt + 1,
                        "retries": retries,
                        "transient": transient,
                        "error_type": type(error).__name__,
                        "error_text": str(error)[:240],
                        "providers": _provider_key_state(self._client),
                    })
                    # #endregion
                    if transient and attempt < retries:
                        continue
                    raise LLMClientError("FreeFlow LLM request failed: {0}".format(error)) from error
            if response is None:
                raise LLMClientError("FreeFlow LLM request failed: {0}".format(last_error))
            content = _response_content(response)
            provider = str(getattr(response, "provider", "") or "unknown")
            model = str(getattr(response, "model", self.model or "") or "default")
            finish_reason = _response_finish_reason(response)

        try:
            parsed = extract_json_object(content)
        except LLMClientError as error:
            if finish_reason == "length":
                raise LLMClientError(
                    "model response truncated (finish_reason=length); "
                    "return a smaller llm-proposal/2.0 with fewer patch operations. "
                    "Original parse error: {0}".format(error)
                ) from error
            raise
        except Exception as error:  # noqa: BLE001
            raise LLMClientError("model response is not valid JSON: {0}".format(error)) from error
        return LLMChatResult(content=content, provider=provider, model=model, parsed=parsed)


_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")


def extract_json_object(text: str) -> Dict[str, Any]:
    """Parse the first JSON object from a model response.

    Uses brace-balanced extraction instead of rfind('}') so truncated
    responses are not silently sliced into invalid mid-object fragments.
    """

    if not isinstance(text, str) or not text.strip():
        raise LLMClientError("model returned an empty response")
    candidates: List[str] = []
    match = _FENCE_RE.search(text)
    if match:
        candidates.append(match.group(1).strip())
    balanced = _extract_balanced_object(text)
    if balanced:
        candidates.append(balanced)
    candidates.append(text.strip())

    last_error: Optional[Exception] = None
    for candidate in candidates:
        for attempt in (candidate, _TRAILING_COMMA_RE.sub(r"\1", candidate)):
            try:
                value = json.loads(attempt)
            except json.JSONDecodeError as error:
                last_error = error
                continue
            if isinstance(value, dict):
                return value
            last_error = LLMClientError("JSON root must be an object")
    if balanced is None and "{" in text:
        raise LLMClientError(
            "model response JSON appears truncated (unbalanced braces): {0}".format(
                last_error or "unknown",
            )
        )
    raise LLMClientError(
        "model response is not a JSON object: {0}".format(last_error or "unknown")
    )


def _extract_balanced_object(text: str) -> Optional[str]:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return None


def _response_finish_reason(response: Any) -> Optional[str]:
    choices = getattr(response, "choices", None)
    if isinstance(choices, list) and choices:
        reason = getattr(choices[0], "finish_reason", None)
        if reason:
            return str(reason)
    if isinstance(response, Mapping):
        raw_choices = response.get("choices")
        if isinstance(raw_choices, list) and raw_choices and isinstance(raw_choices[0], Mapping):
            reason = raw_choices[0].get("finish_reason")
            if reason:
                return str(reason)
    return None


def _response_content(response: Any) -> str:
    if isinstance(response, Mapping):
        return str(response.get("content", ""))
    content = getattr(response, "content", None)
    if content is None:
        return str(response)
    return str(content)
