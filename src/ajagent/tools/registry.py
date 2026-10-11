"""A dictionary dispatch, schema validation, and bounded JSON results."""
from dataclasses import asdict
import json
from jsonschema import Draft202012Validator
from ajagent.core.types import ToolResult
from ajagent.tools.base import ToolError
from ajagent.tools.database import database_tool
from ajagent.tools.files import Workspace


def build_registry(settings):
    workspace = Workspace(settings.workspace, settings.config_path, settings.tool_timeout)
    return {tool.name: tool for tool in [*workspace.specs(), database_tool(workspace)]}


def reject_constant(value):
    raise ValueError("Non-finite JSON numbers are unsupported")


def execute(call, registry):
    if call.name not in registry:
        return ToolResult(call.call_id, False, error={"code": "unknown_tool", "message": "Choose an advertised tool"})
    tool = registry[call.name]
    try:
        args = json.loads(call.arguments, parse_constant=reject_constant)
        if not Draft202012Validator(tool.input_schema).is_valid(args):
            raise ToolError("invalid_arguments", "Arguments must match the advertised JSON schema")
        data, sources = tool.handler(**args)
        return ToolResult(call.call_id, True, data, sources=sources)
    except ValueError:
        error = {"code": "invalid_arguments", "message": "Use a valid UTF-8 file or JSON argument object"}
    except ToolError as failure:
        error = {"code": failure.code, "message": failure.message}
    except OSError:
        error = {"code": "io_error", "message": "Cannot access the requested file or database"}
    except Exception:
        # A failed tool must not prevent other calls in the batch from returning.
        error = {"code": "tool_error", "message": "Tool failed; change the input or choose another tool"}
    return ToolResult(call.call_id, False, error=error)


def serialize(result, limit):
    payload = asdict(result)
    # The ID belongs to the transport envelope, not the unbounded result body.
    payload.pop("call_id")
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    if len(encoded) <= limit:
        return encoded
    preview = json.dumps(result.data, ensure_ascii=False, allow_nan=False)
    payload["data"] = {"truncated": True, "preview": preview[:limit // 4],
                       "hint": "Narrow the query or read a smaller line range"}
    payload["sources"] = []
    encoded = json.dumps(payload, ensure_ascii=False)
    while len(encoded) > limit:
        payload["data"]["preview"] = payload["data"]["preview"][:len(payload["data"]["preview"]) // 2]
        encoded = json.dumps(payload, ensure_ascii=False)
    return encoded
