"""Stateful fixture supporting stdio and Streamable HTTP sessions."""
import argparse
import os
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel


class Reply(BaseModel):
    pid: int
    calls: int


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--transport', choices=['stdio', 'streamable-http'], default='stdio')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    server = FastMCP('persistent-fixture', host='127.0.0.1', port=args.port)
    calls = 0

    @server.tool()
    def count() -> Reply:
        """Return the process ID and call count to verify connection reuse."""
        nonlocal calls
        calls += 1
        return Reply(pid=os.getpid(), calls=calls)

    server.run(transport=args.transport)


if __name__ == '__main__':
    main()
