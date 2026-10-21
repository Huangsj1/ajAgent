"""OpenAI Responses: replay output items, including opaque reasoning."""
from ajagent.core.types import ModelTurn, ToolCall
from ajagent.models.base import HTTPModel


class OpenAIModel(HTTPModel):
    path = "/responses"

    def payload(self, context, tools):
        return {"model": self.settings.model, "max_output_tokens": self.settings.output_tokens, "input": context, "store": False,
                "include": ["reasoning.encrypted_content"],
                "tools": [{"type": "function", **tool.definition(), "strict": False} for tool in tools]}

    def parse(self, data):
        # print(f"OpenAIModel.parse: data = {data}")  # Debugging line
        items = data["output"]
        calls, text, refused = [], [], False
        for item in items:
            if item["type"] == "function_call":
                calls.append(ToolCall(item["call_id"], item["name"], item["arguments"]))
            elif item["type"] == "message":
                for part in item["content"]:
                    if part["type"] == "output_text":
                        text.append(part["text"])
                    elif part["type"] == "refusal":
                        refused = True
                    else:
                        raise ValueError("Unsupported content type")
            elif item["type"] != "reasoning":
                raise ValueError("Unsupported output item")
        answer = "\n".join(text)
        status = "completed" if data["status"] == "completed" else "incomplete"
        if refused:
            status = "refused"
        elif status == "completed" and not calls and not answer.strip():
            status = "empty_output"
        usage = data.get("usage") or {}
        return ModelTurn(answer, calls, {"input_tokens": usage.get("input_tokens", 0),
                                        "output_tokens": usage.get("output_tokens", 0)}, status, items)

    def tool_output(self, result, call_id):
        return {"type": "function_call_output", "call_id": call_id, "output": result}
