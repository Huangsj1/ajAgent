"""HTTP retry policy shared by the two wire-format adapters."""
import time
from typing import Protocol
import httpx
from ajagent.options import DEFAULTS
from ajagent.core.types import ModelTurn


class ModelError(Exception):
    """Structured diagnostics without raw response bodies or request credentials."""

    def __init__(self, message, *, kind='model_error', status=None, attempts=None, request_id=None):
        super().__init__(message)
        self.details = {'kind': kind, 'message': message, 'http_status': status,
                        'attempts': attempts, 'request_id': request_id}



class ModelClient(Protocol):
    def generate(self, context: list[dict], tools: list) -> ModelTurn: ...
    def tool_output(self, result: str, call_id: str) -> dict: ...


class HTTPModel:
    def __init__(self, settings, client, sleep=time.sleep):
        self.settings, self.client, self.sleep = settings, client, sleep

    def post(self, path, payload):
        # Retry only the HTTP request, never the enclosing agent step.
        options = getattr(self.settings, 'options', DEFAULTS).retry
        for attempt in range(options.attempts):
            budget = getattr(self, 'request_budget', None)
            timeout = min(self.settings.timeout, budget.remaining_seconds()) if budget else self.settings.timeout
            try:
                response = self.client.post(
                    self.settings.base_url.rstrip("/") + path, json=payload,
                    headers={"Authorization": "Bearer " + self.settings.api_key},
                    timeout=timeout,
                )
            except httpx.RequestError as error:
                if attempt == options.attempts - 1:
                    raise ModelError(f"Network error after {options.attempts} attempts ({type(error).__name__})",
                                     kind=type(error).__name__, attempts=options.attempts) from None
            else:
                request_id = response.headers.get('x-request-id') or response.headers.get('request-id')
                diagnostics = dict(status=response.status_code, attempts=attempt + 1, request_id=request_id)
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt == options.attempts - 1:
                        raise ModelError(f"HTTP {response.status_code} after {options.attempts} attempts", kind="http_error", **diagnostics)
                elif response.status_code == 402:
                    raise ModelError('HTTP 402: account balance or billing prevents API access; check provider billing before resuming.',
                                     kind='billing_error', **diagnostics)
                elif response.is_error:
                    raise ModelError(f"HTTP {response.status_code}; check model, credentials and parameters", kind="http_error", **diagnostics)
                else:
                    try:
                        return response.json()
                    except ValueError:
                        raise ModelError("Invalid JSON from model API", kind="invalid_json", **diagnostics) from None
            delay = options.backoff_seconds * 2 ** attempt
            self.sleep(min(delay, budget.remaining_seconds()) if budget else delay)

    def generate(self, context, tools):
        try:
            turn = self.parse(self.post(self.path, self.payload(context, tools)))
            ids = [call.call_id for call in turn.tool_calls]
            if len(ids) != len(set(ids)) or any(not isinstance(i, str) or not i for i in ids):
                raise ValueError("Invalid call IDs")
            if any(not isinstance(c.name, str) or not isinstance(c.arguments, str) for c in turn.tool_calls):
                raise ValueError("Invalid tool call")
            return turn
        except (KeyError, IndexError, TypeError, ValueError, AttributeError) as error:
            raise ModelError(f"Invalid or unsupported model response ({type(error).__name__})", kind="invalid_response") from None
