"""Persistent MCP session owned by one async task in a dedicated thread."""
import asyncio
import httpx
from concurrent.futures import Future
from contextlib import AsyncExitStack
from datetime import timedelta
import os
from threading import Thread, Lock
from ajagent.tools.common.base import ToolError


class MCPClient:
    def __init__(self, config):
        self.config = config
        self.ready = Future()
        self.thread = None
        self.loop = self.queue = self.owner = None
        self.tools = []
        self.closed = False
        self.submit_lock = Lock()

    def connect(self):
        if self.thread is None:
            self.thread = Thread(target=self._thread, name=f'mcp-{self.config.name}', daemon=True)
            self.thread.start()
        try:
            self.tools = self.ready.result(timeout=self.config.timeout)
        except Exception:
            self.close()
            raise ToolError('mcp_connect', f'MCP server {self.config.name} could not connect') from None
        return self

    def _thread(self):
        try:
            asyncio.run(self._serve())
        except BaseException:
            if not self.ready.done():
                self.ready.set_exception(ToolError('mcp_connect', 'MCP connection failed'))
        finally:
            self.closed = True
            if self.queue:
                while not self.queue.empty():
                    job = self.queue.get_nowait()
                    if job and not job[2].done():
                        job[2].set_exception(ToolError('mcp_closed', 'MCP connection closed'))

    async def _serve(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        from mcp.client.streamable_http import streamable_http_client
        self.loop, self.queue, self.owner = asyncio.get_running_loop(), asyncio.Queue(), asyncio.current_task()
        config = self.config
        if self.closed:
            return
        async with AsyncExitStack() as stack:
            # All context managers enter/exit in this owner task (AnyIO requirement).
            if config.transport == 'stdio':
                errors = stack.enter_context(open(os.devnull, 'w'))
                transport = stdio_client(StdioServerParameters(command=config.command, args=config.args,
                                          env=config.env or None, cwd=config.cwd), errlog=errors)
            else:
                http = await stack.enter_async_context(httpx.AsyncClient(headers=config.headers, timeout=config.timeout))
                transport = streamable_http_client(config.url, http_client=http)
            streams = await stack.enter_async_context(transport)
            session = await stack.enter_async_context(ClientSession(streams[0], streams[1],
                                                    read_timeout_seconds=timedelta(seconds=config.timeout)))
            async with asyncio.timeout(config.timeout):
                await session.initialize()
                tools, cursor = [], None
                while True:
                    response = await session.list_tools(cursor=cursor)
                    tools.extend(response.tools)
                    cursor = response.nextCursor
                    if not cursor:
                        break
            self.ready.set_result(tools)
            while True:
                job = await self.queue.get()
                if job is None:
                    break
                name, arguments, future, timeout = job
                if future.cancelled():
                    continue
                try:
                    async with asyncio.timeout(timeout):
                        result = await session.call_tool(name, arguments)
                    if not future.done():
                        future.set_result(result)
                except asyncio.CancelledError:
                    if not future.done():
                        future.set_exception(ToolError('mcp_closed', 'MCP connection closed during the call'))
                    raise
                except Exception as error:
                    if not future.done():
                        future.set_exception(ToolError('mcp_call', f'MCP {config.name}: {type(error).__name__}'))
                    # Never silently retry calls with unknown remote effects.

    def call(self, name, arguments, timeout=None):
        future = Future()
        with self.submit_lock:
            if self.closed or self.loop is None or not self.ready.done():
                raise ToolError('mcp_closed', 'MCP client is not connected')
            limit = min(timeout, self.config.timeout) if timeout is not None else self.config.timeout
            self.loop.call_soon_threadsafe(self.queue.put_nowait, (name, arguments, future, limit))
        try:
            return future.result(timeout=limit)
        except TimeoutError:
            future.cancel()
            raise ToolError('mcp_timeout', 'MCP call timed out; remote outcome may be unknown') from None

    def close(self):
        with self.submit_lock:
            if self.closed:
                return
            self.closed = True
            if self.loop and self.loop.is_running():
                self.loop.call_soon_threadsafe(self.owner.cancel)
        if self.thread:
            self.thread.join(timeout=self.config.timeout)
