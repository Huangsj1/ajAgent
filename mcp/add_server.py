"""Minimal stdio MCP server for testing tool discovery and structured results."""
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel

server = FastMCP('addition')


class Addition(BaseModel):
    sum: int


@server.tool()
def add(a: int, b: int) -> Addition:
    """Add two integers."""
    return Addition(sum=a + b)


if __name__ == '__main__':
    server.run(transport='stdio')
