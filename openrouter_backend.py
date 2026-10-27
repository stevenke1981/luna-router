"""OpenRouter adapter for Luna Decisions and separate verified-free text workers.

This module never retries inference or executes tools returned by a model.
Freshness, task eligibility, account-wide rate accounting, and local model
allowlists belong to the caller. A zero catalog price is not authorization to
fall back to another model or to use a paid server tool.
"""

from __future__ import annotations

import copy
from credentials import registry_key, resolve_key
import json
import math
import os
import re
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


BASE_URL = "https://openrouter.ai"
DECISION_MODEL = "openai/gpt-6-luna-decisions"
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_DECISION_BODY_BYTES = 48 * 1024
MAX_DECISION_QUESTIONS = 32
DECISION_INPUT_RESERVE = 4096
SYSTEM_RESERVE = 2048
SYSTEM_PROMPT = (
    "Complete the supplied text task and return the result as text. "
    "You have no external tools in this request. Do not claim that you ran "
    "commands, changed files, or performed external actions."
)
_MODALITIES = {
    "text", "image", "embeddings", "audio", "video", "rerank",
    "decisions", "speech", "transcription", "all",
}
_FREE_ID = re.compile(r"[A-Za-z0-9._~-]+/[A-Za-z0-9._~-]+:free\Z")


class APIError(Exception):
    """An API failure with a safe message, status, and optional retry hint."""

    def __init__(
        self,
        message: str = "OpenRouter request failed",
        status: int | None = None,
        retry_after: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward a bearer token to a redirected endpoint, even on the
        # same origin. urllib converts a declined redirect to HTTPError.
        return None


def _safe_retry_after(headers: Any) -> str | None:
    value = headers.get("Retry-After") if headers is not None else None
    if not isinstance(value, str) or len(value) > 128:
        return None
    if any(ord(char) < 32 or ord(char) > 126 for char in value):
        return None
    value = value.strip()
    if re.fullmatch(r"[0-9]+", value):
        return value
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if parsed.tzinfo is not None else None


def _reject_constant(value: str) -> None:
    raise ValueError("non-finite JSON number")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _check_finite(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite JSON number")
    if isinstance(value, dict):
        for item in value.values():
            _check_finite(item)
    elif isinstance(value, list):
        for item in value:
            _check_finite(item)


def _strict_json(raw: bytes) -> dict[str, Any]:
    value = json.loads(
        raw.decode("utf-8"), parse_constant=_reject_constant,
        object_pairs_hook=_unique_object,
    )
    _check_finite(value)
    if not isinstance(value, dict):
        raise ValueError("JSON root must be an object")
    return value


def _url_for(path: str) -> str:
    if not isinstance(path, str) or not path.startswith("/api/"):
        raise ValueError("Use an OpenRouter /api/ path, not an external URL")
    if any(ord(char) < 33 or ord(char) == 127 for char in path) or "\\" in path:
        raise ValueError("Invalid API path")
    parts = urlsplit(path)
    if parts.scheme or parts.netloc or parts.fragment:
        raise ValueError("External origins and URL fragments are not allowed")
    if any(segment in {".", ".."} for segment in parts.path.split("/")):
        raise ValueError("Relative path segments are not allowed")
    return BASE_URL + path


def request_json(
    method: str, path: str, payload: dict[str, Any] | None = None,
    timeout: float = 20,
) -> dict[str, Any]:
    """Make one request, with no redirects, retry, raw-error logging, or fallback.

    Public model catalog reads omit authorization. All POST requests and other
    GET requests require OPENROUTER_API_KEY, which is read only from the process
    environment. Caller-supplied headers or alternative base URLs are absent.
    """
    url = _url_for(path)
    if method not in {"GET", "POST"}:
        raise ValueError("Only GET and POST are supported")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError("timeout must be a positive finite number")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a positive finite number")
    if method == "GET" and payload is not None:
        raise ValueError("GET requests must not contain a payload")
    public_catalog = method == "GET" and urlsplit(path).path == "/api/v1/models"
    headers = {"Accept": "application/json", "User-Agent": "adaptive-luna-router/2.7.0"}
    token, source = ("", "public") if public_catalog else resolve_key()
    if not public_catalog:
        if not token:
            raise APIError("OPENROUTER_API_KEY is required")
        if any(ord(char) < 33 or ord(char) > 126 for char in token):
            raise APIError("OPENROUTER_API_KEY has an invalid format")
        headers["Authorization"] = "Bearer " + token
    try:
        return _send(method, url, payload, headers, timeout)
    except APIError as exc:
        # A host started before the key was rotated (e.g. the Codex app) passes
        # a stale process value. 401 means nothing was processed, so retry once
        # with the live Windows User/System value when it differs.
        fresh, _ = registry_key() if exc.status == 401 and source == "process" else ("", "")
        if not fresh or fresh == token or any(ord(char) < 33 or ord(char) > 126 for char in fresh):
            raise
        headers["Authorization"] = "Bearer " + fresh
        return _send(method, url, payload, headers, timeout)


def _send(method, url, payload, headers, timeout) -> dict[str, Any]:
    data = None
    if payload is not None:
        try:
            data = json.dumps(
                payload, ensure_ascii=False, allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeEncodeError):
            raise APIError("Request payload must be finite UTF-8 JSON") from None
        headers["Content-Type"] = "application/json"
    req = Request(url, data=data, headers=headers, method=method)
    try:
        with build_opener(_NoRedirect()).open(req, timeout=timeout) as response:
            status = response.status
            retry_after = _safe_retry_after(response.headers)
            # Defense in depth: a custom opener must not silently change URL.
            if response.geturl() != url:
                raise APIError("Redirected responses are not allowed", status)
            if status < 200 or status >= 300:
                raise APIError("OpenRouter returned an HTTP error", status, retry_after)
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise APIError("OpenRouter response exceeds the size limit", status)
    except HTTPError as exc:
        raise APIError(
            "OpenRouter returned an HTTP error", exc.code,
            _safe_retry_after(exc.headers),
        ) from None
    except (URLError, OSError, TimeoutError):
        raise APIError("OpenRouter transport failed") from None
    try:
        result = _strict_json(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise APIError("OpenRouter returned invalid JSON", status) from None
    if "error" in result:
        reported = result["error"]
        code = reported.get("code") if isinstance(reported, dict) else None
        if isinstance(code, bool) or not isinstance(code, int) or not 100 <= code <= 599:
            code = status
        raise APIError("OpenRouter returned an API error", code, retry_after)
    return result


def catalog(modality: str = "text") -> dict[str, Any]:
    """Fetch the complete public catalog for an output modality.

    No q=free text search is used: the caller receives model records and must
    use price and capability checks. Omitting offset and limit requests the
    full list according to the current API contract.
    """
    if modality not in _MODALITIES:
        raise ValueError("Unknown output modality")
    path = "/api/v1/models?" + urlencode({"output_modalities": modality})
    result = request_json("GET", path)
    records = result.get("data")
    if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
        raise APIError("OpenRouter returned an invalid model catalog", 200)
    links = result.get("links")
    if isinstance(links, dict) and links.get("next") is not None:
        raise APIError("OpenRouter returned an incomplete model catalog", 200)
    return {
        "source": BASE_URL + path,
        "fetched_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "simulation": False,
        "data": records,
    }


def _zero_price(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        return False
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, OverflowError):
        return False
    return number.is_finite() and number >= 0 and number == 0


def _not_expired(value: Any) -> bool:
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            deadline = datetime.combine(date.fromisoformat(value), time(), timezone.utc)
        else:
            deadline = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if deadline.tzinfo is None:
                return False
        return datetime.now(timezone.utc) < deadline
    except (ValueError, OverflowError):
        return False


def valid_free(model: Any, modality: str = "text") -> bool:
    """Fail closed unless a catalog record has explicit zero pricing.

    Structured/unknown pricing entries are rejected rather than assuming that
    a pricing override is free. The special openrouter/free alias is eligible,
    but it randomly chooses a compatible free model; it is not a quality ranker.
    """
    if not isinstance(model, dict) or modality not in _MODALITIES - {"all"}:
        return False
    identifier = model.get("id")
    if not isinstance(identifier, str):
        return False
    if identifier != "openrouter/free" and not _FREE_ID.fullmatch(identifier):
        return False
    pricing = model.get("pricing")
    if not isinstance(pricing, dict) or not {"prompt", "completion"}.issubset(pricing):
        return False
    if not all(_zero_price(value) for value in pricing.values()):
        return False
    architecture = model.get("architecture")
    if not isinstance(architecture, dict):
        return False
    outputs = architecture.get("output_modalities")
    if not isinstance(outputs, list) or modality not in outputs:
        return False
    context = model.get("context_length")
    if isinstance(context, bool) or not isinstance(context, int) or context <= 0:
        return False
    return _not_expired(model.get("expiration_date"))


def get_free_models(catalog: dict[str, Any], modality: str = "text") -> list[dict[str, Any]]:
    records = catalog.get("data") if isinstance(catalog, dict) else None
    if not isinstance(records, list):
        raise ValueError("catalog must contain a data list")
    return [row for row in records if valid_free(row, modality)]


def _free_provider() -> dict[str, Any]:
    return {
        "allow_fallbacks": True,
        "require_parameters": True,
        "max_price": {"prompt": 0, "completion": 0, "request": 0, "image": 0},
    }


def complete(
    model_record: dict[str, Any], task_text: str, max_tokens: int = 2048,
) -> dict[str, Any]:
    """Call one verified free text model and report a bounded response state.

    The conservative context check uses UTF-8 bytes as an input upper bound,
    a system/framing reserve, and twice the requested output tokens. Passing
    it is not a guarantee for every tokenizer/provider; provider limits remain
    authoritative. Returned tool calls are only flagged, never executed.
    """
    if not valid_free(model_record, "text"):
        raise ValueError("A current, zero-priced free text model record is required")
    if not isinstance(task_text, str) or not task_text.strip():
        raise ValueError("task_text must contain text")
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0:
        raise ValueError("max_tokens must be a positive integer")
    try:
        input_bound = len(task_text.encode("utf-8")) + SYSTEM_RESERVE + max_tokens * 2
    except UnicodeEncodeError:
        raise ValueError("task_text must be valid UTF-8 text") from None
    if input_bound * 5 > model_record["context_length"] * 4:
        raise ValueError("Task exceeds the conservative context budget")
    top = model_record.get("top_provider")
    output_limit = top.get("max_completion_tokens") if isinstance(top, dict) else None
    if isinstance(output_limit, int) and not isinstance(output_limit, bool) and output_limit > 0:
        if max_tokens > output_limit:
            raise ValueError("max_tokens exceeds the provider's reported output limit")
    identifier = model_record["id"]
    body = {
        "model": identifier,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": task_text},
        ],
        "max_tokens": max_tokens,
        "stream": False,
        "provider": _free_provider(),
    }
    result = request_json("POST", "/api/v1/chat/completions", body)
    choices = result.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise APIError("OpenRouter returned no usable completion choice", 200)
    first = choices[0]
    message = first.get("message")
    actual_model = result.get("model")
    if not isinstance(message, dict) or not isinstance(actual_model, str) or not actual_model.strip():
        raise APIError("OpenRouter returned an invalid completion envelope", 200)
    content = message.get("content")
    if content is not None and not isinstance(content, str):
        raise APIError("OpenRouter returned a non-text completion", 200)
    content = content or ""
    finish_reason = first.get("finish_reason")
    tool_calls_present = bool(message.get("tool_calls") or message.get("function_call"))
    if tool_calls_present or finish_reason in {"tool_calls", "function_call"}:
        status = "tool_calls_received"
        tool_calls_present = True
    elif finish_reason == "length":
        status = "incomplete_length"
    elif not content.strip():
        status = "empty_response"
    elif finish_reason != "stop":
        status = "incomplete_response"
    else:
        status = "response_received"
    usage = result.get("usage", {})
    if not isinstance(usage, dict):
        raise APIError("OpenRouter returned invalid usage metadata", 200)
    # A zero-priced candidate + provider.max_price=0 are the free-worker
    # safeguards. Provider account spend is managed by OpenRouter, not a local
    # monetary ledger; a surprising reported cost is surfaced, never retried.
    cost = reported_cost(result)
    warnings = []
    if "cost" in usage and cost is None:
        warnings.append("unparseable_provider_cost")
    elif cost is not None and Decimal(cost) > 0:
        warnings.append("provider_reported_nonzero_cost_for_free_worker")
    return {
        "status": status,
        "requested_model": identifier,
        "actual_model": actual_model,
        "text": content,
        "usage": usage,
        "reported_cost_usd": cost,
        "warnings": warnings,
        "finish_reason": finish_reason,
        "tool_calls_present": tool_calls_present,
    }


def reported_cost(response: Any) -> str | None:
    """Best-effort provider usage reporting, NOT a monetary policy gate.

    A malformed or missing usage cost is unknown, not zero. This must not
    prevent an otherwise valid OpenRouter response from being used.
    """
    usage = response.get("usage") if isinstance(response, dict) else None
    value = usage.get("cost") if isinstance(usage, dict) else None
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        return None
    try:
        cost = Decimal(str(value))
    except (ValueError, InvalidOperation, OverflowError):
        return None
    return format(cost, "f") if cost.is_finite() and cost >= 0 else None


def _decision_model_record(record: Any, policy: Any) -> int:
    """Check identity, modalities, expiration, and available context only.

    OpenRouter, NOT this application, enforces the user's monetary limits.
    This intentionally does not read prices, token charges or account balance.
    """
    if not isinstance(policy, dict) or policy.get("model") != DECISION_MODEL or policy.get("enabled") is not True:
        raise ValueError("Only enabled GPT-6 Luna Decisions is supported")
    if not isinstance(record, dict) or record.get("id") != DECISION_MODEL:
        raise ValueError("Decision model record does not match the configured model")
    architecture = record.get("architecture")
    if not isinstance(architecture, dict):
        raise ValueError("Decision model capabilities are missing")
    inputs = architecture.get("input_modalities")
    outputs = architecture.get("output_modalities")
    if not isinstance(inputs, list) or "text" not in inputs:
        raise ValueError("Decision model must accept text")
    if not isinstance(outputs, list) or "decisions" not in outputs:
        raise ValueError("Decision model must return decisions")
    context = record.get("context_length")
    if isinstance(context, bool) or not isinstance(context, int) or context <= 0:
        raise ValueError("Decision model context length is invalid")
    if not _not_expired(record.get("expiration_date")):
        raise ValueError("Decision model is expired or its expiration is invalid")
    return context


def get_decision_model(
    catalog: dict[str, Any], decision_policy: dict[str, Any],
) -> dict[str, Any] | None:
    """Look up an exact decision model; no price check, local USD cap or paid fallback."""
    if not isinstance(decision_policy, dict) or decision_policy.get("model") != DECISION_MODEL or decision_policy.get("enabled") is not True:
        raise ValueError("Only enabled GPT-6 Luna Decisions is supported")
    records = catalog.get("data") if isinstance(catalog, dict) else None
    if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
        raise ValueError("Decision catalog must contain model objects")
    matches = [row for row in records if row.get("id") == DECISION_MODEL]
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError("Decision catalog contains duplicate model entries")
    _decision_model_record(matches[0], decision_policy)
    return matches[0]


def _decision_body(body: Any, policy: Any, context_length: int) -> dict[str, Any]:
    if not isinstance(body, dict) or body.get("model") != DECISION_MODEL:
        raise ValueError("Decisions requests must use GPT-6 Luna Decisions")
    if set(body) - {"model", "state", "questions", "provider"}:
        raise ValueError("Unsupported Decisions request fields")
    questions = body.get("questions")
    if "state" not in body or not isinstance(questions, dict) or not 1 <= len(questions) <= MAX_DECISION_QUESTIONS:
        raise ValueError("Decisions requests require state and 1 to 32 named questions")
    for name, question in questions.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(question, dict):
            raise ValueError("Decision questions must be named typed objects")
        if not isinstance(question.get("type"), str) or question["type"] not in {"choice", "noul", "score"}:
            raise ValueError("Unsupported decision question type")
    incoming = body.get("provider", {})
    if not isinstance(incoming, dict):
        raise ValueError("Decision provider preferences must be an object")
    allowed = {"data_collection", "zdr", "enforce_distillable_text", "allow_fallbacks", "require_parameters"}
    if set(incoming) - allowed:
        raise ValueError("Unsupported decision provider preferences")
    outgoing = copy.deepcopy(body)
    provider = {"allow_fallbacks": True, "require_parameters": True}
    if "data_collection" in incoming:
        if incoming["data_collection"] not in ("allow", "deny"):
            raise ValueError("Invalid provider data_collection")
        provider["data_collection"] = incoming["data_collection"]
    for key in ("zdr", "enforce_distillable_text"):
        if key in incoming:
            if not isinstance(incoming[key], bool):
                raise ValueError("Invalid provider privacy flag")
            provider[key] = incoming[key]
    for key in ("allow_fallbacks", "require_parameters"):
        if key in incoming and not isinstance(incoming[key], bool):
            raise ValueError("Invalid provider flag")
    # Account/API-key/Guardrail spend settings live on OpenRouter. Deliberately
    # no max_price in paid decision request (free worker retains zero max_price).
    outgoing["provider"] = provider
    try:
        encoded = json.dumps(outgoing, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        raise ValueError("Decision request must contain finite UTF-8 JSON") from None
    if len(encoded) > MAX_DECISION_BODY_BYTES:
        raise ValueError("Decision request exceeds the 48 KiB body limit")
    token_bound = (len(encoded) + DECISION_INPUT_RESERVE) * len(questions)
    if token_bound * 5 > context_length * 4:
        raise ValueError("Decision request exceeds the conservative context budget")
    return outgoing


def decide(body: dict[str, Any], model_record: dict[str, Any] | None, decision_policy: dict[str, Any]) -> dict[str, Any]:
    """Send one decision POST without local price checking or spending ledger."""
    if model_record is None:
        # Strict online mode calls the exact documented endpoint directly.
        # This is a conservative payload envelope, NOT an invented model catalog.
        if decision_policy.get("model") != DECISION_MODEL or decision_policy.get("enabled") is not True:
            raise ValueError("Only enabled GPT-6 Luna Decisions is supported")
        context_length = 65536
    else:
        context_length = _decision_model_record(model_record, decision_policy)
    outgoing = _decision_body(body, decision_policy, context_length)
    return request_json("POST", "/api/alpha/decisions", outgoing)


def get_quota() -> dict[str, int]:
    """Return only the free-request daily counter; never return key metadata."""
    result = request_json("GET", "/api/v1/key")
    data = result.get("data")
    quota = data.get("free_model_daily_requests") if isinstance(data, dict) else None
    if not isinstance(quota, dict):
        raise APIError("Free-request quota information is unavailable", 200)
    fields = ("used", "limit", "remaining")
    if any(
        isinstance(quota.get(key), bool)
        or not isinstance(quota.get(key), int)
        or quota[key] < 0
        for key in fields
    ):
        raise APIError("OpenRouter returned invalid free-request quota information", 200)
    return {key: quota[key] for key in fields}


def get_spend_status() -> dict[str, Any]:
    """Read the current API key's OpenRouter credit cap; no model inference.

    Return only spend settings, not API key, token, label, hash, creator, or
    usage history. A missing key cap is clearly not reported as a zero cap.
    This does not attest to organization guardrails or whole-account policy.
    """
    result = request_json("GET", "/api/v1/key")
    data = result.get("data")
    if not isinstance(data, dict):
        raise APIError("OpenRouter returned an invalid API key status", 200)
    limit = data.get("limit")
    remaining = data.get("limit_remaining")
    reset = data.get("limit_reset")
    if limit is None:
        if reset is not None:
            raise APIError("OpenRouter returned inconsistent key limit metadata", 200)
        formatted_limit = None
    else:
        formatted_limit = _format_key_money(limit)
    formatted_remaining = None if remaining is None else _format_key_money(remaining)
    if reset is not None and reset not in {"daily", "weekly", "monthly"}:
        raise APIError("OpenRouter returned unknown key limit reset", 200)
    return {
        "provider": "openrouter",
        "api_key_limit_configured": formatted_limit is not None,
        "limit_usd": formatted_limit,
        "limit_remaining_usd": formatted_remaining,
        "limit_reset": reset,
        "account_or_guardrail_limits": "not_checked",
        "local_fee_check": False,
    }


def _format_key_money(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise APIError("OpenRouter returned invalid key spending metadata", 200)
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, OverflowError):
        raise APIError("OpenRouter returned invalid key spending metadata", 200) from None
    if not number.is_finite():
        raise APIError("OpenRouter returned invalid key spending metadata", 200)
    return format(number, "f")
