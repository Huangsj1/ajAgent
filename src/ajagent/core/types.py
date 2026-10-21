"""Shared semantics; provider wire items remain opaque."""
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    call_id: str
    name: str
    arguments: str


@dataclass
class ModelTurn:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    stop_reason: str = "completed"
    continuation_items: list[dict] = field(default_factory=list)


@dataclass
class ToolResult:
    call_id: str
    ok: bool
    data: Any = None
    error: dict | None = None
    sources: list[dict] = field(default_factory=list)


@dataclass
class RunResult:
    status: str
    answer: str
    steps: int
    usage: dict = field(default_factory=dict)
    last_usage: dict | None = None
