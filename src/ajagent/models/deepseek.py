"""DeepSeek Chat Completions messages and tool results."""
from ajagent.core.types import ModelTurn, ToolCall
from ajagent.models.base import HTTPModel


class DeepSeekModel(HTTPModel):
    path = "/chat/completions"

    def payload(self, context, tools):
        return {"model": self.settings.model, "max_tokens": (getattr(self, 'request_output_tokens', None) or self.settings.output_tokens), "messages": context, "stream": False,
                "thinking": {"type": self.settings.thinking},
                "tools": [{"type": "function", "function": tool.definition()} for tool in tools]}

    def parse(self, data):
        choice = data["choices"][0]
        message, reason = choice["message"], choice["finish_reason"]
        calls = []
        for call in message.get("tool_calls") or []:
            if call["type"] != "function":
                raise ValueError("Unsupported tool type")
            calls.append(ToolCall(call["id"], call["function"]["name"], call["function"]["arguments"]))
        status = "completed" if reason in ("stop", "tool_calls") else "incomplete"
        if message.get("refusal") or reason == "content_filter":
            status = "refused"
        text = message.get("content") or ""
        if status == "completed" and not calls and (not text.strip() or reason == "tool_calls"):
            status = "empty_output"
        usage = data.get("usage") or {}
        return ModelTurn(text, calls, {"input_tokens": usage.get("prompt_tokens", 0),
                                      "output_tokens": usage.get("completion_tokens", 0)}, status, [message])

    def tool_output(self, result, call_id):
        return {"role": "tool", "tool_call_id": call_id, "content": result}
