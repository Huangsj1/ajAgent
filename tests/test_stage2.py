"""Protocol, tool, and loop checks use only temporary data and mock HTTP."""
import argparse
import json
from pathlib import Path
import sqlite3

import httpx
import pytest

from ajagent.config import Settings, load_config
from ajagent.core.loop import run_agent
from ajagent.core.types import ModelTurn, ToolCall
from ajagent.models.base import ModelError
from ajagent.models.deepseek import DeepSeekModel
from ajagent.models.openai import OpenAIModel
from ajagent.tools.common.base import ToolSpec, schema
from ajagent.tools.common.registry import execute, serialize
from ajagent.tools.files.workspace import Workspace
from ajagent.tools.database.sqlite import database_tool


def basic_registry(settings):
    workspace = Workspace(settings.workspace, settings.config_path, settings.tool_timeout, settings.options.tools)
    return {tool.name: tool for tool in [*workspace.specs(), database_tool(workspace)]}


@pytest.fixture
def settings(tmp_path):
    (tmp_path / "note.txt").write_text("Alpha\nBeta\n")
    return Settings("deepseek", "fake-model", "fake-key", "https://example.test", tmp_path, tmp_path / "custom.toml")


def chat(calls=None, text="done", reason=None):
    message = {"role": "assistant", "content": text, "reasoning_content": "opaque"}
    if calls:
        message["tool_calls"] = [{"id": c.call_id, "type": "function", "function": {
            "name": c.name, "arguments": c.arguments}} for c in calls]
    return {"choices": [{"message": message, "finish_reason": reason or ("tool_calls" if calls else "stop")}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2}}


def responses(calls=None, text="done"):
    items = [{"type": "reasoning", "id": "r", "summary": [], "encrypted_content": "opaque"}]
    if calls:
        items.extend({"type": "function_call", "id": "item-" + c.call_id,
                      "call_id": c.call_id, "name": c.name, "arguments": c.arguments} for c in calls)
    else:
        items.append({"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]})
    return {"status": "completed", "output": items, "usage": {"input_tokens": 10, "output_tokens": 2}}


@pytest.mark.parametrize("adapter,make", [(DeepSeekModel, chat), (OpenAIModel, responses)])
def test_both_protocols_batch_and_retry_do_not_repeat_tools(settings, adapter, make):
    calls = [ToolCall("bad", "missing", "{}"), ToolCall("good", "read_file", '{"path":"note.txt"}')]
    requests, sleeps = [], []
    responses_to_send = iter([(200, make(calls)), (503, {}), (200, make())])

    def handler(request):
        requests.append(json.loads(request.content))
        status, data = next(responses_to_send)
        return httpx.Response(status, json=data)

    seen = []
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_agent("read", adapter(settings, client, sleeps.append), basic_registry(settings),
                           on_tool=lambda _, call, result: seen.append(call.call_id))
    assert result.status == "completed"
    assert result.usage == {"input_tokens": 20, "output_tokens": 4}
    assert seen == ["bad", "good"]
    assert sleeps == [0.5]
    assert requests[1] == requests[2]
    if adapter is DeepSeekModel:
        history = requests[1]["messages"]
        assert history[2]["reasoning_content"] == "opaque"
        outputs = history[-2:]
        assert [o["tool_call_id"] for o in outputs] == ["bad", "good"]
        assert json.loads(outputs[0]["content"])["ok"] is False
    else:
        history = requests[1]["input"]
        assert history[2]["encrypted_content"] == "opaque"
        assert [o["call_id"] for o in history[-2:]] == ["bad", "good"]
        assert requests[0]["store"] is False
        assert requests[0]["tools"][0]["type"] == "function"
        assert "function" not in requests[0]["tools"][0]


@pytest.mark.parametrize("status,count", [(401, 1), (400, 1), (429, 3), (500, 3)])
def test_retry_limits_and_safe_errors(settings, status, count):
    requests, sleeps = [], []
    def handler(request):
        requests.append(request)
        return httpx.Response(status, text="SECRET")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ModelError) as error:
            DeepSeekModel(settings, client, sleeps.append).generate([], [])
    assert len(requests) == count
    assert "SECRET" not in str(error.value)
    assert sleeps == ([0.5, 1.0] if count == 3 else [])


def test_network_timeout_is_bounded(settings):
    sleeps = []
    def handler(request):
        raise httpx.ReadTimeout("SECRET", request=request)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ModelError, match="3 attempts"):
            OpenAIModel(settings, client, sleeps.append).generate([], [])
    assert sleeps == [0.5, 1.0]


@pytest.mark.parametrize("adapter,data,status", [
    (DeepSeekModel, chat(text="", reason="stop"), "empty_output"),
    (DeepSeekModel, chat(reason="length"), "incomplete"),
    (DeepSeekModel, chat(reason="content_filter"), "refused"),
    (OpenAIModel, {"status": "incomplete", "output": []}, "incomplete"),
    (OpenAIModel, responses(text=""), "empty_output"),
    (OpenAIModel, {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal"}]}]}, "refused"),
])
def test_stop_reasons(settings, adapter, data, status):
    assert adapter(settings, None).parse(data).stop_reason == status


def test_unknown_output_is_not_silently_dropped(settings):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={
        "status": "completed", "output": [{"type": "unsupported_tool_call"}]}))) as client:
        with pytest.raises(ModelError):
            OpenAIModel(settings, client).generate([], [])


def invoke(settings, name, args):
    return execute(ToolCall("id", name, json.dumps(args)), basic_registry(settings))


@pytest.mark.parametrize("path", ["../outside", "config.local.toml", ".env", ".ajagent/data", "custom.toml"])
def test_files_exclude_secrets_and_outside_paths(settings, path):
    result = invoke(settings, "read_file", {"path": path})
    assert not result.ok and result.error["code"] == "forbidden"


def test_symlinks_are_excluded_from_all_file_tools(settings):
    (settings.workspace / "alias").symlink_to(settings.workspace / "note.txt")
    assert not invoke(settings, "read_file", {"path": "alias"}).ok
    assert invoke(settings, "list_files", {}).data["files"] == ["note.txt"]
    assert len(invoke(settings, "search_files", {"query": "Alpha"}).sources) == 1


def test_read_search_empty_and_limits(settings):
    assert invoke(settings, "read_file", {"path": "note.txt", "start_line": 2}).data["lines"] == [{"line": 2, "text": "Beta"}]
    assert invoke(settings, "search_files", {"query": "alpha"}).sources[0]["line"] == 1
    empty = invoke(settings, "search_files", {"query": "absent"})
    assert empty.ok and empty.data["matches"] == [] and empty.sources == []
    assert invoke(settings, "read_file", {"path": "absent"}).error["code"] == "not_found"
    (settings.workspace / "huge.txt").write_text("x" * 1_000_001)
    assert invoke(settings, "read_file", {"path": "huge.txt"}).error["code"] == "too_large"
    (settings.workspace / "long.txt").write_text('"' * 10000)
    encoded = serialize(invoke(settings, "read_file", {"path": "long.txt"}), 1000)
    assert len(encoded) <= 1000 and json.loads(encoded)["data"]["truncated"]


@pytest.mark.parametrize("arguments", ['{', '[]', '{"path":1}', '{"path":"note.txt","max_lines":false}', '{"path":"note.txt","extra":1}'])
def test_invalid_tool_arguments(settings, arguments):
    result = execute(ToolCall("id", "read_file", arguments), basic_registry(settings))
    assert not result.ok and result.error["code"] == "invalid_arguments"


def test_sql_read_only_row_limit_and_timeout(settings):
    path = settings.workspace / "demo.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE notes (value INTEGER)")
        conn.executemany("INSERT INTO notes VALUES (?)", [(1,), (2,), (3,)])
    result = invoke(settings, "query_sqlite", {"path": "demo.db", "query": "SELECT * FROM notes", "max_rows": 2})
    assert result.ok and result.data["rows"] == [[1], [2]] and result.data["truncated"]
    for query in ("DELETE FROM notes", "ATTACH DATABASE ':memory:' AS other", "SELECT * FROM missing"):
        assert invoke(settings, "query_sqlite", {"path": "demo.db", "query": query}).error["code"] == "sql_error"
    settings.tool_timeout = 0.001
    query = "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n) SELECT sum(x) FROM n"
    assert invoke(settings, "query_sqlite", {"path": "demo.db", "query": query}).error["code"] == "timeout"
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 3


class FakeModel:
    def __init__(self, change=False):
        self.count = 0
        self.change = change
    def generate(self, context, tools):
        self.count += 1
        args = '{"query":"%s"}' % (self.count if self.change else "absent")
        return ModelTurn(tool_calls=[ToolCall(str(self.count), "search_files", args)])
    def tool_output(self, result, call_id):
        return {"role": "tool", "tool_call_id": call_id, "content": result}


def test_no_progress_and_step_limit(settings):
    model = FakeModel()
    assert run_agent("task", model, basic_registry(settings)).status == "no_progress"
    assert model.count == 3
    changing = FakeModel(change=True)
    assert run_agent("task", changing, basic_registry(settings), max_steps=4).status == "step_limit"
    assert changing.count == 4


def test_tool_exception_does_not_escape(settings):
    def fail():
        raise RuntimeError("SECRET")
    tool = ToolSpec("fail", "fail", schema({}, []), fail)
    result = execute(ToolCall("id", "fail", "{}"), {"fail": tool})
    assert result.error["code"] == "tool_error"
    assert "SECRET" not in serialize(result, 1000)


def test_config_provider_binding_and_precedence(tmp_path, monkeypatch):
    for key in list(__import__('os').environ):
        if key.startswith(("AJAGENT_", "DEEPSEEK_", "OPENAI_")):
            monkeypatch.delenv(key)
    config = tmp_path / "local.toml"
    config.write_text('[deepseek]\napi_key="ds-key"\nmodel="ds"\n[openai]\napi_key="oa-key"\nmodel="gpt"\n')
    args = argparse.Namespace(config=str(config), provider="openai", workspace=str(tmp_path))
    monkeypatch.setenv("OPENAI_MODEL", "env-model")
    settings = load_config(args)
    assert settings.api_key == "oa-key" and settings.model == "env-model"
    assert "oa-key" not in repr(settings)
    args.model = "cli-model"
    assert load_config(args).model == "cli-model"
    config.write_text('[openai]\napi_key="key"\nmodel="gpt"\napi_mode="chat_completions"')
    with pytest.raises(ValueError):
        load_config(args)


def test_escaped_output_stays_within_limit(settings):
    (settings.workspace / "control.txt").write_text("\x01" * 10000)
    result = invoke(settings, "read_file", {"path": "control.txt"})
    assert len(serialize(result, 1000)) <= 1000


def test_sql_nonfinite_value_remains_serializable(settings):
    with sqlite3.connect(settings.workspace / "demo.db"):
        pass
    result = invoke(settings, "query_sqlite", {"path": "demo.db", "query": "SELECT 1e999 AS large"})
    assert result.ok and json.loads(serialize(result, 1000))["data"]["rows"] == [["inf"]]


def test_file_scan_timeout(settings, monkeypatch):
    import ajagent.tools.files.workspace as files
    clock = iter([0, 10])
    monkeypatch.setattr(files.time, "monotonic", lambda: next(clock))
    assert invoke(settings, "list_files", {}).error["code"] == "timeout"
