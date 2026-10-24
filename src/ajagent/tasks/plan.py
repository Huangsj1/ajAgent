"""Bounded configuration for durable task planning."""
from dataclasses import dataclass


@dataclass(frozen=True)
class TaskOptions:
    compact_on_checkpoint: bool = True
    max_idle_steps: int = 12
    max_tasks: int = 20
    text_chars: int = 8000
    max_checks: int = 5
    max_paths: int = 50

    @classmethod
    def load(cls, config):
        options = cls(**config.get('tasks', {}))
        if type(options.compact_on_checkpoint) is not bool or any(type(value) is not int or value < 1 for name, value in vars(options).items() if name != 'compact_on_checkpoint'):
            raise ValueError('Task limits must be positive integers')
        return options

