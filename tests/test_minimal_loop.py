"""Exercise the real HTTP/message boundary without credentials or network."""

import argparse
import copy
import importlib.util
import json
from pathlib import Path

import httpx
import pytest

SPEC = importlib.util.spec_from_file_location(
    "minimal_agent", Path(__file__).resolve().parents[1] / "examples/minimal_agent.py"
)
agent = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agent)
SETTINGS = {"api_key": "fake-test-key", "model": "fake-model",
            "base_url": "https://example.test", "max_steps": 8, "timeout": 1}


def call(name, arguments, call_id="call-1"):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)}}


def reply(content=None, calls=None, reason=None, **extra):
    message = {"role": "assistant", "content": content, **extra}
    if calls:
        message["tool_calls"] = calls
    return {"choices": [{"message": message,
                         "finish_reason": reason or ("tool_calls" if calls else "stop")} ]}


def run(replies, settings=None):
    requests = []
    responses = iter(replies)

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=next(responses))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = agent.run_agent("A demo task", settings or SETTINGS, client)
    return result, requests


def test_direct_answer():
    result, requests = run([reply("Paris")])
    assert result == {"status": "completed", "answer": "Paris"}
    assert len(requests) == 1


def test_lookup_then_add_preserves_call_ids_and_continuation():
    result, requests = run([
        reply(calls=[call("lookup_note", {"topic": "study"}, "note")], reasoning_content="opaque"),
        reply(calls=[call("add", {"a": 25, "b": 35}, "sum")]),
        reply("60 minutes"),
    ])
    history = requests[-1]["messages"]
    assert result["status"] == "completed"
    assert history[2]["reasoning_content"] == "opaque"
    assert [m["tool_call_id"] for m in history if m["role"] == "tool"] == ["note", "sum"]
    assert json.loads(history[-1]["content"])["data"] == 60


def test_batch_returns_every_result_including_unknown_tool():
    result, requests = run([
        reply(calls=[call("missing", {}, "bad"), call("add", {"a": 2, "b": 3}, "good")]),
        reply("5"),
    ])
    outputs = requests[1]["messages"][-2:]
    assert [m["tool_call_id"] for m in outputs] == ["bad", "good"]
    assert [json.loads(m["content"])["ok"] for m in outputs] == [False, True]
    assert result["status"] == "completed"


@pytest.mark.parametrize("name,args", [
    ("add", {"a": "2", "b": 3}), ("add", {"a": True, "b": 1}),
    ("add", {"a": 1}), ("add", {"a": float("inf"), "b": 1}),
    ("add", {"a": 1, "b": 2, "extra": 3}), ("add", []),
    ("lookup_note", {"topic": []}),
])
def test_invalid_arguments(name, args):
    assert agent.execute_tool(call(name, args))["ok"] is False


def test_malformed_json_and_empty_lookup():
    bad = call("add", {})
    bad["function"]["arguments"] = "{"
    assert agent.execute_tool(bad)["ok"] is False
    assert agent.execute_tool(call("lookup_note", {"topic": "missing"}))["data"] == {"found": False}


def test_eight_step_limit_is_not_success():
    result, requests = run([reply(calls=[call("add", {"a": 1, "b": 2})])] * 8)
    assert result["status"] == "step_limit"
    assert len(requests) == 8


@pytest.mark.parametrize("response,status", [
    (reply(""), "empty_output"),
    (reply("partial", reason="length"), "incomplete"),
    (reply(reason="content_filter"), "refused"),
    (reply(refusal="No"), "refused"),
])
def test_non_success_outputs(response, status):
    assert run([response])[0]["status"] == status


def test_config_precedence(tmp_path, monkeypatch):
    for key in ("DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "AJAGENT_PROVIDER",
                "AJAGENT_MAX_STEPS", "AJAGENT_TIMEOUT"):
        monkeypatch.delenv(key, raising=False)
    path = tmp_path / "config.toml"
    path.write_text('[deepseek]\napi_key="local-key"\nmodel="local-model"\n')
    args = argparse.Namespace(config=str(path), model=None, max_steps=None, timeout=None)
    assert agent.load_config(args)["model"] == "local-model"
    monkeypatch.setenv("DEEPSEEK_MODEL", "env-model")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "env-key")
    assert agent.load_config(args)["model"] == "env-model"
    args.model, args.max_steps = "cli-model", 2
    settings = agent.load_config(args)
    assert (settings["model"], settings["api_key"], settings["max_steps"]) == ("cli-model", "env-key", 2)
    args.max_steps = 0
    with pytest.raises(ValueError):
        agent.load_config(args)


@pytest.mark.parametrize("mode,status", [("http", "api_error"), ("timeout", "network_error"),
                                       ("malformed", "invalid_data")])
def test_cli_errors_do_not_echo_sensitive_bodies(mode, status, monkeypatch, capsys):
    def handler(request):
        if mode == "timeout":
            raise httpx.ReadTimeout("secret-body", request=request)
        return httpx.Response(401 if mode == "http" else 200, text="secret-body")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(agent.httpx, "Client", lambda: client)
    monkeypatch.setattr(agent, "load_config", lambda args: copy.deepcopy(SETTINGS))
    monkeypatch.setattr(agent.sys, "argv", ["minimal_agent.py", "demo"])
    assert agent.main() == 1
    output = capsys.readouterr().out
    assert status in output
    assert "secret-body" not in output and SETTINGS["api_key"] not in output


def test_example_stays_small():
    lines = Path(SPEC.origin).read_text().splitlines()
    assert 50 <= sum(bool(line.strip()) for line in lines) <= 150
