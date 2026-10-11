"""Tool metadata and safe, structured failures."""
from dataclasses import dataclass
from typing import Callable


class ToolError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict
    handler: Callable

    def definition(self):
        return {"name": self.name, "description": self.description, "parameters": self.input_schema}


def schema(properties, required):
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}
