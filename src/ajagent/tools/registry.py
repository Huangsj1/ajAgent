"""A dictionary dispatch, schema validation, and bounded JSON results."""
from dataclasses import asdict
import json
from jsonschema import Draft202012Validator
from ajagent.core.types import ToolResult
from ajagent.tools.base import ToolError
from ajagent.options import DEFAULTS, MIN_TOOL_OUTPUT_CHARS
from ajagent.tools.database.sqlite import database_tool
from ajagent.tools.files.workspace import Workspace


def build_registry(settings):
    workspace = Workspace(settings.workspace, settings.config_path, settings.tool_timeout, getattr(settings, 'options', DEFAULTS).tools)
    return {tool.name: tool for tool in [*workspace.specs(), database_tool(workspace)]}


def reject_constant(value):
    raise ValueError("Non-finite JSON numbers are unsupported")


def prepare(call, registry):
    """Resolve a tool and validate its arguments once, before permission checks."""
    if call.name not in registry:
        raise ToolError('unknown_tool', 'Choose an advertised tool')
    tool = registry[call.name]
    try:
        args = json.loads(call.arguments, parse_constant=reject_constant)
    except (ValueError, TypeError):
        raise ToolError('invalid_arguments', 'Use a valid JSON argument object') from None
    if not Draft202012Validator(tool.input_schema).is_valid(args):
        raise ToolError('invalid_arguments', 'Arguments must match the advertised JSON schema')
    return tool, args


def failure(call_id, code, message):
    return ToolResult(call_id, False, error={'code': code, 'message': message})


def invoke(call_id, tool, args):
    """Execute validated arguments; handler failures remain structured results."""
    try:
        data, sources = tool.handler(**args)
        return ToolResult(call_id, True, data, sources=sources)
    except ToolError as error:
        return failure(call_id, error.code, error.message)
    except ValueError:
        return failure(call_id, 'invalid_arguments', 'Use a valid UTF-8 file or JSON argument object')
    except OSError:
        return failure(call_id, 'io_error', 'Cannot access the requested file or database')
    except Exception:
        return failure(call_id, 'tool_error', 'Tool failed; change the input or choose another tool')


def execute(call, registry):
    try:
        tool, args = prepare(call, registry)
    except ToolError as error:
        return failure(call.call_id, error.code, error.message)
    return invoke(call.call_id, tool, args)


def serialize(result, limit):
    if limit < MIN_TOOL_OUTPUT_CHARS:
        raise ValueError(f'Tool output budget must be at least {MIN_TOOL_OUTPUT_CHARS}')
    payload = asdict(result)
    # The ID belongs to the transport envelope, not the unbounded result body.
    payload.pop("call_id")
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    if len(encoded) <= limit:
        return encoded
    preview = json.dumps(result.data, ensure_ascii=False, allow_nan=False)
    payload["data"] = {"truncated": True, "preview": preview[:limit // 4],
                       "hint": "Narrow the query or read a smaller line range"}
    if isinstance(result.data, dict) and 'artifact' in result.data:
        payload['data']['artifact'] = result.data['artifact']
    payload["sources"] = []
    encoded = json.dumps(payload, ensure_ascii=False)
    while len(encoded) > limit:
        preview = payload['data']['preview']
        message = (payload.get('error') or {}).get('message', '')
        if preview:
            payload['data']['preview'] = preview[:len(preview) // 2]
        elif message:
            payload['error']['message'] = message[:len(message) // 2]
        else:
            # Even malformed oversized error codes cannot stall serialization.
            return json.dumps({'ok': result.ok, 'data': {'truncated': True}, 'sources': [],
                               'error': None if result.ok else {'code': 'output_too_large', 'message': ''}})
        encoded = json.dumps(payload, ensure_ascii=False)
    return encoded
