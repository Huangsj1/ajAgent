"""HTTP retry policy shared by the two wire-format adapters."""
import time
from typing import Protocol
import httpx
from ajagent.core.types import ModelTurn


class ModelError(Exception):
    """A safe error message that excludes raw API responses."""


class ModelClient(Protocol):
    def generate(self, context: list[dict], tools: list) -> ModelTurn: ...
    def tool_output(self, result: str, call_id: str) -> dict: ...


class HTTPModel:
    def __init__(self, settings, client, sleep=time.sleep):
        self.settings, self.client, self.sleep = settings, client, sleep

    def post(self, path, payload):
        # Retry only the HTTP request, never the enclosing agent step.
        for attempt in range(3):
            budget = getattr(self, 'request_budget', None)
            timeout = min(self.settings.timeout, budget.remaining_seconds()) if budget else self.settings.timeout
            try:
                response = self.client.post(
                    self.settings.base_url.rstrip("/") + path, json=payload,
                    headers={"Authorization": "Bearer " + self.settings.api_key},
                    timeout=timeout,
                )
            except httpx.RequestError:
                if attempt == 2:
                    raise ModelError("Network error after 3 attempts") from None
            else:
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt == 2:
                        raise ModelError(f"HTTP {response.status_code} after 3 attempts")
                elif response.is_error:
                    raise ModelError(f"HTTP {response.status_code}; check model, credentials and parameters")
                else:
                    try:
                        return response.json()
                    except ValueError:
                        raise ModelError("Invalid JSON from model API") from None
            delay = 0.5 * 2 ** attempt
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
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            raise ModelError("Invalid or unsupported model response") from None
