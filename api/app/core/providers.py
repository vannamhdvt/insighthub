"""Small REST adapters share bounded timeouts and sanitized transport errors."""

import logging
import httpx

from app.core.config import get_settings
from app.core.errors import BudgetExceeded, GuardrailBlocked, ProviderError

logger = logging.getLogger("insighthub.providers")


def _gateway_error(response: httpx.Response) -> ProviderError:
    """Map an OpenAI-compatible gateway error to a fixed public error.

    Only the error *type/marker* is inspected; the body is never logged or returned.
    """
    request_id = response.headers.get("x-litellm-call-id")
    marker = ""
    try:
        error = response.json().get("error") or {}
        marker = f"{error.get('type', '')} {error.get('message', '')}"[:2000].lower()
    except (ValueError, AttributeError):
        pass
    if response.status_code in {400, 403, 422} and "insighthub_guardrail" in marker:
        exc: ProviderError = GuardrailBlocked()
    elif "budget_exceeded" in marker or "budget has been exceeded" in marker:
        exc = BudgetExceeded()
    else:
        exc = ProviderError()
    exc.request_id = request_id
    return exc


def post_json(url: str, *, headers: dict, payload: dict) -> dict:
    try:
        # Do not inherit proxies, follow redirects, or log response bodies/URLs.
        with httpx.Client(
            timeout=get_settings().provider_timeout_seconds,
            trust_env=False,
            follow_redirects=False,
        ) as client:
            response = client.post(url, headers=headers, json=payload)
            if response.status_code >= 400:
                logger.warning("AI provider request failed (HTTP %s)", response.status_code)
                raise _gateway_error(response)
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("Invalid JSON object")
            call_id = response.headers.get("x-litellm-call-id")
            if call_id:
                # Gateway call id: unique per request, joins app logs with LiteLLM spend logs.
                data["_gateway_call_id"] = call_id
            return data
    except ProviderError:
        raise
    except (httpx.HTTPError, ValueError):
        logger.warning("AI provider request failed")
        raise ProviderError() from None


def token_count(value) -> int | None:
    return value if type(value) is int and value >= 0 else None


def indexed_embeddings(data: dict, count: int) -> list:
    items = data["data"]
    if len(items) != count or any(type(item.get("index")) is not int for item in items):
        raise ProviderError()
    if sorted(item["index"] for item in items) != list(range(count)):
        raise ProviderError()
    return [item["embedding"] for item in sorted(items, key=lambda item: item["index"])]
