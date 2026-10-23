# MCP 测试服务

此目录只放测试/学习用服务器，客户端在 `src/ajagent/tools/mcp/`。

- `add_server.py`：stdio，提供 `add(a, b)`，用于工具发现、权限和结构化响应测试。
- `state_server.py`：stdio 或 Streamable HTTP，提供 `count()` 返回 PID 和调用次数，用于连接复用、重连、多服务测试。计数本身会改变服务状态。

```sh
uv sync --extra mcp
uv run python mcp/state_server.py --transport streamable-http --port 8000
```

HTTP 地址为 `http://127.0.0.1:8000/mcp`。在 `config/mcp.local.toml` 配置对应 http server，或使用默认 add stdio 示例（由客户端自动启动）。在 `config/tools.local.toml` 的 enabled 添加 `mcp` 后生效。

自动验证：`uv run --extra mcp pytest tests/test_mcp_system.py -q`。测试复用这些脚本，不依赖外部服务或 API key。
