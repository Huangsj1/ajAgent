"""Named MCP servers, with credentials kept in an ignored local TOML file."""
from dataclasses import dataclass, field
from pathlib import Path
import os
import re
import sys
import tomllib
from ajagent.options import DEFAULTS


@dataclass
class ServerConfig:
    name: str
    transport: str = 'stdio'
    command: str = ''
    args: list = field(default_factory=list)
    cwd: str | None = None
    url: str = ''
    env: dict = field(default_factory=dict, repr=False)
    headers: dict = field(default_factory=dict, repr=False)
    timeout: float = DEFAULTS.capabilities.mcp_timeout
    read_tools: list = field(default_factory=list)
    enabled: bool = True


def load_servers(path, timeout=DEFAULTS.capabilities.mcp_timeout):
    path = Path(path)
    raw = tomllib.loads(path.read_text(encoding='utf-8'))
    if set(raw) - {'servers'}:
        raise ValueError('MCP config accepts only [servers.NAME] sections')
    servers = []
    for name, values in raw.get('servers', {}).items():
        if not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_-]*', name):
            raise ValueError('Invalid MCP server name')
        config = ServerConfig(name=name, **{'timeout': timeout, **values})
        if type(config.enabled) is not bool:
            raise ValueError('MCP enabled must be boolean')
        if not config.enabled:
            continue
        if config.transport not in {'stdio', 'http'}:
            raise ValueError('MCP transport must be stdio or http (Streamable HTTP)')
        if not isinstance(config.timeout, (int, float)) or isinstance(config.timeout, bool) or not 0 < config.timeout < float('inf'):
            raise ValueError('MCP timeout must be positive')
        for items in (config.args, config.read_tools):
            if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
                raise ValueError('MCP args/read_tools must be string lists')
        for mapping in (config.env, config.headers):
            if not isinstance(mapping, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in mapping.items()):
                raise ValueError('MCP env/headers must be string mappings')
            for key, value in mapping.items():
                def expand(match):
                    if match[1] not in os.environ:
                        raise ValueError('Missing MCP credential environment variable')
                    return os.environ[match[1]]
                mapping[key] = re.sub(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}', expand, value)
        if config.transport == 'stdio':
            if not isinstance(config.command, str) or not config.command:
                raise ValueError('stdio requires command')
            if config.command == '${PYTHON}':
                config.command = sys.executable
            config.cwd = str((path.parent / (config.cwd or '.')).resolve())
        elif not isinstance(config.url, str) or not config.url.startswith(('http://', 'https://')):
            raise ValueError('HTTP MCP requires an HTTP(S) endpoint URL')
        servers.append(config)
    return servers
