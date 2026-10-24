"""One explicit selection of tool groups, independent of subsystem settings."""
from dataclasses import dataclass
from pathlib import Path

GROUPS = frozenset({'files', 'database', 'execution', 'web', 'browser', 'retrieval', 'skills', 'delegation', 'mcp', 'artifacts', 'tasks'})


@dataclass(frozen=True)
class ToolConfig:
    enabled: tuple = ('files', 'database', 'execution', 'web', 'skills', 'delegation', 'artifacts', 'tasks')
    disabled: tuple = ()
    skills_path: str = 'skills'
    mcp_config: str = 'mcp.local.toml'

    @classmethod
    def load(cls, config):
        values = dict(config.get('tools', {}))
        if set(values) - cls.__dataclass_fields__.keys():
            raise ValueError('Unknown tools setting')
        for key in ('enabled', 'disabled'):
            if key in values:
                if not isinstance(values[key], list) or not all(isinstance(item, str) for item in values[key]):
                    raise ValueError(f'tools.{key} must be a list of names')
                values[key] = tuple(values[key])
        result = cls(**values)
        if set(result.enabled) - GROUPS:
            raise ValueError('Unknown tool group')
        if not all(isinstance(value, str) and value for value in (result.skills_path, result.mcp_config)):
            raise ValueError('Tool configuration paths must be nonempty strings')
        return result

    def path(self, value, config_path):
        return (Path(config_path).parent / Path(value).expanduser()).resolve()
