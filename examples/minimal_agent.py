"""A small DeepSeek tool loop; run from the repository root with uv."""

import argparse
import json
import math
import os
from pathlib import Path
import sys
from ajagent.options import DEFAULT_CONFIG_PATH, read_config

import httpx

NOTES = {"study": {"reading_minutes": 25, "practice_minutes": 35}}
TOOLS = [
    {"type": "function", "function": {
        "name": "add", "description": "Add two finite numbers.",
        "parameters": {"type": "object", "properties": {
            "a": {"type": "number"}, "b": {"type": "number"}},
            "required": ["a", "b"], "additionalProperties": False}}},
    {"type": "function", "function": {
        "name": "lookup_note", "description": "Look up the demo study plan; topic: study.",
        "parameters": {"type": "object", "properties": {"topic": {"type": "string"}},
            "required": ["topic"], "additionalProperties": False}}},
]


def add(a, b):
    if any(type(n) not in (int, float) or not math.isfinite(n) for n in (a, b)):
        raise ValueError("Expected finite numbers")
    total = a + b
    if not math.isfinite(total):
        raise ValueError("Result is not finite")
    return total


def lookup_note(topic):
    if not isinstance(topic, str):
        raise ValueError("Expected a string topic")
    return NOTES.get(topic, {"found": False})


def execute_tool(call):
    handlers = {"add": add, "lookup_note": lookup_note}
    try:
        args = json.loads(call["function"]["arguments"])
        if not isinstance(args, dict):
            raise ValueError("Expected an argument object")
        return {"ok": True, "data": handlers[call["function"]["name"]](**args)}
    except (KeyError, TypeError, ValueError, OverflowError):
        return {"ok": False, "error": "Unknown tool or invalid arguments; check the tool schema."}


def load_config(args):
    path = Path(args.config)
    config = read_config(path)
    runtime, provider = config.get("runtime", {}), config.get("deepseek", {})
    if os.getenv("AJAGENT_PROVIDER", runtime.get("provider", "deepseek")) != "deepseek":
        raise ValueError("Stage 1 only supports DeepSeek")
    if provider.get("api_mode", "chat_completions") != "chat_completions":
        raise ValueError("Stage 1 requires chat_completions")
    settings = {
        "api_key": os.getenv("DEEPSEEK_API_KEY", provider.get("api_key", "")),
        "model": args.model or os.getenv("DEEPSEEK_MODEL", provider.get("model", "")),
        "base_url": provider.get("base_url", "https://api.deepseek.com"),
        "max_steps": args.max_steps if args.max_steps is not None else int(
            os.getenv("AJAGENT_MAX_STEPS", runtime.get("max_steps", 8))),
        "timeout": args.timeout if args.timeout is not None else float(
            os.getenv("AJAGENT_TIMEOUT", runtime.get("timeout", 30))),
    }
    if not settings["api_key"] or not settings["model"]:
        raise ValueError("Configure a DeepSeek API key and model")
    if settings["max_steps"] < 1 or not 0 < settings["timeout"] < float("inf"):
        raise ValueError("Step limit and timeout must be positive")
    return settings


def run_agent(prompt, settings, client):
    messages = [
        {"role": "system", "content": "Use tools for demo notes and arithmetic. Never invent tool results."},
        {"role": "user", "content": prompt},
    ]
    for step in range(1, settings["max_steps"] + 1):
        response = client.post(
            settings["base_url"].rstrip("/") + "/chat/completions",
            headers={"Authorization": "Bearer " + settings["api_key"]},
            json={"model": settings["model"], "messages": messages, "tools": TOOLS,
                  "thinking": {"type": "disabled"}, "stream": False},
            timeout=settings["timeout"],
        )
        # print(f"Step {step} | HTTP {response.status_code}")
        # print(f"Response: {response.json()}")
        response.raise_for_status()
        choice = response.json()["choices"][0]
        message, reason = choice["message"], choice["finish_reason"]
        if message.get("refusal") or reason == "content_filter":
            return {"status": "refused", "answer": "The model declined this request."}
        if reason not in ("stop", "tool_calls"):
            return {"status": "incomplete", "answer": "Model generation ended before completion."}
        # Preserve the assistant message, including any protocol continuation fields.
        messages.append(message)
        calls = message.get("tool_calls") or []
        if not calls:
            answer = message.get("content") or ""
            status = "completed" if answer.strip() and reason == "stop" else "empty_output"
            return {"status": status, "answer": answer}
        for call in calls:
            result = execute_tool(call)
            print(f"Step {step} | {call['function']['name']} | {json.dumps(result, ensure_ascii=False)}")
            messages.append({"role": "tool", "tool_call_id": call["id"],
                             "content": json.dumps(result, ensure_ascii=False)})
    return {"status": "step_limit", "answer": "Step limit reached; the task is unfinished."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--model")
    parser.add_argument("--max-steps", type=int)
    parser.add_argument("--timeout", type=float)
    args = parser.parse_args()
    try:
        settings = load_config(args)
        print(f"Provider: deepseek | Model: {settings['model']}")
        with httpx.Client() as client:
            result = run_agent(args.prompt, settings, client)
    except httpx.HTTPStatusError as error:
        result = {"status": "api_error", "answer": f"HTTP {error.response.status_code}; check credentials, model, or service."}
    except httpx.RequestError:
        result = {"status": "network_error", "answer": "Request failed or timed out; no retry was made."}
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        # Do not echo configuration values, raw responses, or exception bodies.
        result = {"status": "invalid_data", "answer": "Invalid configuration or model response; check inputs and protocol."}
    except KeyboardInterrupt:
        result = {"status": "cancelled", "answer": "Interrupted by user."}
    print(f"Status: {result['status']}\n{result['answer']}")
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
