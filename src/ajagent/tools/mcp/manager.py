"""Manage named clients and adapt discovered tools to the common registry."""
from functools import partial
import re
from ajagent.tools.common.base import ToolError, ToolSpec
from ajagent.tools.mcp.client import MCPClient


class MCPManager:
    def __init__(self, configs, remaining=None):
        self.configs = configs
        self.remaining = remaining
        self.clients = {}

    def connect(self):
        try:
            for config in self.configs:
                self.clients[config.name] = MCPClient(config).connect()
        except BaseException:
            self.close()
            raise
        return self

    def close(self):
        for client in self.clients.values():
            client.close()
        self.clients.clear()

    def reconnect(self, name):
        """Explicit reconnection; never replay a prior tool call."""
        config = self.clients[name].config
        self.clients[name].close()
        self.clients[name] = MCPClient(config).connect()

    def call(self, server, tool, /, **arguments):
        response = self.clients[server].call(tool, arguments, self.remaining() if self.remaining else None)
        if response.isError:
            raise ToolError('mcp_tool_error', f'{server}/{tool} returned an error')
        return {'content': [item.model_dump(mode='json', exclude_none=True) for item in response.content],
                'structured_content': response.structuredContent}, [
                    {'uri': f'mcp://{server}/{tool}', 'kind': 'mcp_result'}]

    def specs(self):
        specs, seen = [], set()
        for name, client in self.clients.items():
            for tool in client.tools:
                key = re.sub(r'[^a-zA-Z0-9_-]', '_', f'mcp_{name}_{tool.name}')
                if key in seen or len(key) > 64:
                    raise ValueError('MCP tool name collision or name longer than 64 characters')
                seen.add(key)
                # Remote annotations are advisory; only local configuration grants read status.
                effect = 'read' if tool.name in client.config.read_tools else 'execute'
                specs.append(ToolSpec(key, tool.description or tool.name, tool.inputSchema,
                                      partial(self.call, name, tool.name), effect))
        return specs
