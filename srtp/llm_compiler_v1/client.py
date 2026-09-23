"""OpenRouter Responses transport with bounded retries and strict JSON extraction."""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence

import httpx
from .env import load_compiler_env, openrouter_api_key

OPENROUTER_RESPONSES_URL = "https://openrouter.ai/api/v1/responses"
DEFAULT_MODEL = "openai/gpt-6-sol"
DEFAULT_HTTP_TIMEOUT_S = 180.0
# Responses counts reasoning and visible output against this budget.
DEFAULT_MAX_TOKENS = 32768
DEFAULT_CHAT_RETRIES = 2
DEFAULT_MAX_REQUESTS = 12

# Canonical Responses error_type takes priority over a generic error.code.
# Never echo upstream error.message: it can contain request/source/key data.
_ERROR_DETAILS = {
    "authentication": "Invalid API key. Replace OPENROUTER_API_KEY in the repository-root .env.",
    "permission_denied": "Access denied. Check OpenRouter key permissions, privacy/guardrail settings and regional availability.",
    "payment_required": "OpenRouter credits or key spending limit cannot cover this request. Check https://openrouter.ai/settings/credits and the key limit; repeated retries will not add credits.",
    "rate_limit_exceeded": "Rate limit reached at OpenRouter or the upstream provider. Wait before retrying.",
    "not_found": "Model unavailable. Check CUBEENGINE_LLM_OPENROUTER_MODEL and OpenRouter model availability.",
    "invalid_request": "Request rejected. Check the selected model's Responses API, JSON and reasoning parameter support.",
    "context_length_exceeded": "Input exceeds the selected model's context limit. Reduce source evidence size.",
    "max_tokens_exceeded": "Requested output budget exceeds the model limit. Check CUBEENGINE_LLM_MAX_TOKENS.",
    "token_limit_exceeded": "Request exceeds the selected model's token limit. Check input size and CUBEENGINE_LLM_MAX_TOKENS.",
    "content_policy_violation": "Provider rejected this request for content policy; no generated IR was accepted.",
    "refusal": "Provider refused this request; no generated IR was accepted.",
    "provider_overloaded": "Upstream model service overloaded after bounded retries. Try again later.",
    "provider_unavailable": "No available OpenRouter provider can serve this model with the required parameters. Try again later or check routing/privacy settings.",
    "server_error": "OpenRouter or upstream service unavailable after bounded retries. Try again later.",
    "timeout": "OpenRouter or upstream request timed out after bounded retries.",
}
_ERROR_ALIASES = {
    "invalid_api_key": "authentication", "model_not_found": "not_found",
    "insufficient_quota": "payment_required", "credit_balance_exhausted": "payment_required",
    "invalid_parameter": "invalid_request", "invalid_prompt": "invalid_request",
    "string_too_long": "invalid_request", "unprocessable": "invalid_request",
    "payload_too_large": "invalid_request", "precondition_failed": "invalid_request",
}
_TRANSIENT_ERRORS = {"rate_limit_exceeded", "provider_overloaded", "provider_unavailable", "server_error", "timeout"}


def _error_context(body, status):
    error = body.get("error")
    error = error if isinstance(error, dict) else {}
    metadata = error.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    for candidate in (body.get("error_type"), metadata.get("error_type"), error.get("type"), error.get("code")):
        if isinstance(candidate, str):
            kind = _ERROR_ALIASES.get(candidate, candidate)
            if kind in _ERROR_DETAILS:
                return kind, metadata
    code = error.get("code")
    effective_status = code if isinstance(code, int) and not isinstance(code, bool) else status
    kind = {400:"invalid_request", 401:"authentication", 402:"payment_required",
            403:"permission_denied", 404:"not_found", 408:"timeout", 409:"server_error",
            413:"invalid_request", 422:"invalid_request", 429:"rate_limit_exceeded",
            502:"server_error", 503:"provider_unavailable", 504:"timeout"}.get(effective_status)
    return kind or ("server_error" if effective_status >= 500 else "unknown"), metadata


class LLMClientError(RuntimeError):
    """Model output cannot be accepted; semantic repair may be attempted."""


class LLMTransportError(LLMClientError):
    """Transport, account or refusal failure: do not run semantic repair."""


def _compiler_json(content):
    try:
        return json.loads(content)
    except json.JSONDecodeError as error:
        if error.msg!='Extra data': raise
        # Multiple complete read-only tool requests can be batched without
        # losing information. Never merge competing definitions or trim data.
        decoder=json.JSONDecoder(); offset=0; requests=[]; count=0
        while offset<len(content):
            while offset<len(content) and content[offset].isspace():offset+=1
            if offset==len(content):break
            value,offset=decoder.raw_decode(content,offset);count+=1
            if not isinstance(value,dict) or set(value)!={'source_requests'} or not isinstance(value['source_requests'],list):
                raise error
            requests.extend(value['source_requests'])
        if count<2 or not 1<=len(requests)<=16:raise error
        return {'source_requests':requests}


@dataclass(frozen=True)
class LLMChatResult:
    content: str
    provider: str
    model: str
    parsed: Mapping[str, Any]


def _resolve_chat_retries():
    try:
        return min(5, max(0, int(os.environ.get("CUBEENGINE_LLM_CHAT_RETRIES", DEFAULT_CHAT_RETRIES))))
    except ValueError:
        return DEFAULT_CHAT_RETRIES


def _resolve_timeout_s():
    try:
        return max(5.0, float(os.environ.get("CUBEENGINE_LLM_TIMEOUT_S", DEFAULT_HTTP_TIMEOUT_S)))
    except ValueError:
        return DEFAULT_HTTP_TIMEOUT_S


def _resolve_max_tokens(explicit):
    if explicit is not None:
        return max(256, int(explicit))
    try:
        return max(256, int(os.environ.get("CUBEENGINE_LLM_MAX_TOKENS", DEFAULT_MAX_TOKENS)))
    except ValueError:
        return DEFAULT_MAX_TOKENS


class OpenRouterLLMClient:
    """One gateway and explicit model; no alternate model or key rotation.

    Pure chat_fn injection is retained for offline compiler tests. Production
    uses Responses JSON mode plus the engine's own full IR validators, because
    the authoring schema permits dynamic maps and source-tool request replies.
    """
    def __init__(self, *, temperature=0.1, max_tokens=None, model=None,
                 chat_fn=None, timeout_s=None, transport=None):
        self.temperature = temperature  # Offline compatibility; never sent with reasoning.
        self._explicit_model = model
        self._explicit_tokens = max_tokens
        self._explicit_timeout = timeout_s
        self._chat_fn = chat_fn
        self._transport = transport
        self._client = None
        self.model = model or DEFAULT_MODEL
        self.provider = "mock" if chat_fn else "openrouter"
        self.max_tokens = _resolve_max_tokens(max_tokens)
        self.timeout_s = timeout_s or _resolve_timeout_s()
        self.reasoning_effort = "medium"
        self.http_requests = 0
        self.max_requests = DEFAULT_MAX_REQUESTS
        self.usage_summary = {'http_requests':0, 'input_tokens':0, 'output_tokens':0, 'reported_cost_usd':None}

    @property
    def cache_identity(self):
        return {"provider":self.provider, "model":self.model,
                "reasoning_effort":self.reasoning_effort, "max_output_tokens":self.max_tokens,
                "endpoint":OPENROUTER_RESPONSES_URL, "output_format":"json_object",
                "routing":{"require_parameters":True}}

    def __enter__(self):
        self.http_requests = 0
        self.usage_summary = {'http_requests':0, 'input_tokens':0, 'output_tokens':0, 'reported_cost_usd':None}
        if self._chat_fn is None:
            load_compiler_env(override=True)
            key = openrouter_api_key()
            self.model = self._explicit_model or os.environ.get("CUBEENGINE_LLM_OPENROUTER_MODEL") or DEFAULT_MODEL
            self.model = self.model.strip()
            if not re.fullmatch(r"[a-zA-Z0-9_-]+/[a-zA-Z0-9_.:-]+", self.model):
                raise LLMTransportError("Use an explicit OpenRouter model ID such as openai/gpt-6-sol in CUBEENGINE_LLM_OPENROUTER_MODEL.")
            self.reasoning_effort = os.environ.get("CUBEENGINE_LLM_REASONING_EFFORT", "medium").strip()
            if self.reasoning_effort not in {"none", "low", "medium", "high", "xhigh", "max"}:
                raise LLMTransportError("Invalid CUBEENGINE_LLM_REASONING_EFFORT; use none/low/medium/high/xhigh/max.")
            self.max_tokens = _resolve_max_tokens(self._explicit_tokens)
            self.timeout_s = self._explicit_timeout or _resolve_timeout_s()
            try:
                self.max_requests = max(1, min(100, int(os.environ.get('CUBEENGINE_LLM_MAX_REQUESTS', DEFAULT_MAX_REQUESTS))))
            except ValueError:
                raise LLMTransportError('CUBEENGINE_LLM_MAX_REQUESTS must be an integer.') from None
            self._client = httpx.Client(
                timeout=httpx.Timeout(self.timeout_s, connect=20.0),
                headers={"Authorization":"Bearer " + key, "Content-Type":"application/json",
                         "X-OpenRouter-Title":"CubeEngine"},
                transport=self._transport, follow_redirects=False,
            )
        return self

    def __exit__(self, *args):
        if self._client is not None:
            self._client.close()
            self._client = None

    def chat_json(self, messages, *, temperature=None, max_tokens=None):
        if self._chat_fn is not None:
            response = self._chat_fn(messages=[dict(item) for item in messages],
                temperature=self.temperature if temperature is None else temperature,
                max_tokens=self.max_tokens if max_tokens is None else int(max_tokens))
            if _response_finish_reason(response) == "length":
                raise LLMClientError("model response truncated (finish_reason=length)")
            content = _response_content(response)
            return LLMChatResult(content, str(getattr(response, "provider", "mock") or "mock"),
                str(getattr(response, "model", "mock") or "mock"), extract_json_object(content))
        if self._client is None:
            raise LLMTransportError("OpenRouterLLMClient must be used as a context manager")
        payload = {
            "model":self.model, "input":[dict(item) for item in messages],
            "instructions":"Return exactly one JSON object for the requested compiler stage. No Markdown fences or commentary.",
            "text":{"format":{"type":"json_object"}}, "store":False,
            "max_output_tokens":self.max_tokens if max_tokens is None else int(max_tokens),
            "reasoning":{"effort":self.reasoning_effort},
            "provider":{"require_parameters":True}, "stream":False,
        }
        body = self._request(payload)
        if body.get("status") == "incomplete":
            reason = (body.get("incomplete_details") or {}).get("reason", "unknown")
            if reason == "content_filter":
                raise LLMTransportError("OpenRouter stopped this response for content filtering; no generated IR was accepted.")
            raise LLMClientError("OpenRouter response incomplete ({0}); no partial JSON was accepted. "
                "For max_output_tokens, increase CUBEENGINE_LLM_MAX_TOKENS or reduce reasoning/output size.".format(reason))
        if body.get("status") != "completed":
            raise LLMTransportError("OpenRouter response did not complete; status={0}.".format(body.get("status", "missing")))
        messages = [item for item in body.get('output', []) if item.get('type')=='message']
        # Responses may contain commentary and a separate final message. They
        # are not fragments of one JSON object and must never be concatenated.
        final = [item for item in messages if item.get('channel')=='final']
        selected = final if final else [item for item in messages if item.get('channel') not in ('analysis','commentary')]
        texts = []
        for item in selected:
            if item.get("type") != "message":
                continue  # Reasoning is not the generated IR.
            for part in item.get("content", []):
                if part.get("type") == "refusal":
                    raise LLMTransportError("OpenRouter refused this compiler request; no generated IR was accepted.")
                if part.get("type") == "output_text":
                    texts.append(part.get("text", ""))
        content = "".join(texts)
        # Production JSON mode must return a complete JSON object. Do not use
        # the legacy brace/fence recovery to mask trailing or truncated output.
        try:
            parsed = _compiler_json(content)
            if not isinstance(parsed, dict):
                raise ValueError("root must be an object")
        except (ValueError, TypeError) as error:
            failure=LLMClientError("OpenRouter output is not one complete JSON object: " + str(error))
            # Preserve only generated text for local diagnosis/repair, never
            # headers, credentials or provider reasoning.
            failure.response_evidence={'response_id':body.get('id'),'content':content,
                'message_channels':[m.get('channel') for m in messages]}
            raise failure from error
        return LLMChatResult(content, "openrouter", str(body.get("model") or self.model), parsed)

    def _request(self, payload):
        retries = _resolve_chat_retries()
        for attempt in range(retries + 1):
            if self.http_requests >= self.max_requests:
                raise LLMTransportError('Stopped before another paid request: CUBEENGINE_LLM_MAX_REQUESTS={0} reached for this job. Accepted stages are checkpointed; review diagnostics before raising the limit.'.format(self.max_requests))
            self.http_requests += 1
            self.usage_summary['http_requests'] = self.http_requests
            try:
                response = self._client.post(OPENROUTER_RESPONSES_URL, json=payload)
            except httpx.TransportError as error:
                if attempt < retries:
                    time.sleep(min(30.0, 2.0 ** (attempt + 1)))
                    continue
                # Do not expose HTTP objects, headers, or raw provider messages.
                raise LLMTransportError("OpenRouter connection failed after {0} attempt(s): {1}. Check network access to openrouter.ai.".format(attempt + 1, type(error).__name__)) from None
            try:
                body = response.json()
            except ValueError:
                body = None
            usage = body.get('usage') if isinstance(body, dict) else None
            if isinstance(usage, dict):
                for field in ('input_tokens', 'output_tokens'):
                    if type(usage.get(field)) is int and usage[field] >= 0:
                        self.usage_summary[field] += usage[field]
                cost = usage.get('cost')
                if type(cost) in (float, int) and 0 <= cost < 1_000_000:
                    self.usage_summary['reported_cost_usd'] = (self.usage_summary['reported_cost_usd'] or 0.0) + cost
            if response.is_success:
                if not isinstance(body, dict):
                    raise LLMTransportError("OpenRouter returned a non-JSON or invalid response envelope.")
                # Responses can return HTTP 200 with a failed body. Do not spend
                # another paid generation or pass partial output to IR repair.
                if not body.get("error") and body.get("status") != "failed":
                    return body
            body = body if isinstance(body, dict) else {}
            status = response.status_code
            kind, metadata = _error_context(body, status)
            inflight = kind == "payment_required" and metadata.get("limit_source") == "openrouter_in_flight_budget"
            transient = kind in _TRANSIENT_ERRORS and not response.is_success
            retry_after = 0.0
            try:
                retry_after = max(0.0, float(response.headers.get("retry-after", "0")))
            except ValueError:
                pass
            if inflight:
                transient = 0 < retry_after <= 30 and not response.is_success
            if retry_after > 30:
                transient = False  # Do not retry before the advertised wait.
            delay = max(min(30.0, 2.0 ** (attempt + 1)), retry_after)
            if transient and attempt < retries:
                time.sleep(delay)
                continue
            detail = _ERROR_DETAILS.get(kind, "Request failed. Check OpenRouter activity logs; no generated IR was accepted.")
            if inflight:
                detail = "OpenRouter in-flight spending budget is temporarily occupied. Wait for running/recent requests to settle before retrying."
            elif kind == "payment_required" and metadata.get("limit_source") == "openrouter_key_limit":
                detail = "OpenRouter API key spending limit reached. Raise the key limit or wait for its configured reset."
            elif kind == "payment_required" and metadata.get("reason") == "weight_exceeds_budget":
                detail = "This request's estimated cost exceeds the OpenRouter credit budget. Add OpenRouter credits or reduce CUBEENGINE_LLM_MAX_TOKENS/input size; retrying unchanged will not help."
            if retry_after > 0:
                detail += " Retry-After: {0:g} seconds.".format(retry_after)
            request_id = response.headers.get("x-request-id") or body.get("id", "")
            suffix = " Request ID: " + request_id if isinstance(request_id, str) and re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", request_id) else ""
            raise LLMTransportError("OpenRouter HTTP {0}: {1} HTTP attempts: {2}.{3}".format(status, detail, attempt + 1, suffix))


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
