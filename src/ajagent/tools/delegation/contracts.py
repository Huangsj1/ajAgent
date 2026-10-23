"""Explicit handoff goals and acceptance criteria for isolated child work."""
from ajagent.tools.common.base import schema


def contract_schema(limits):
    text = {'type': 'string', 'minLength': 1, 'maxLength': limits.task_chars}
    return schema({'goal': text, 'context': {'type': 'string', 'maxLength': limits.task_chars},
                   'acceptance_criteria': {'type': 'array', 'minItems': 1, 'items': text},
                   'allowed_paths': {'type': 'array', 'minItems': 1, 'items': text}},
                  ['goal', 'context', 'acceptance_criteria', 'allowed_paths'])
