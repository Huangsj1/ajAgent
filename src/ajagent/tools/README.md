# 工具层

工具统一通过 `runtime/loader.py` 的 ToolManager 加载。配置入口是 `config/tools.local.toml` 的 `[tools].enabled`，不需要为不同工具另设 CLI 开关。完整命令见[项目 README](../../../README.md)。

| 目录 | 职责 |
| --- | --- |
| `common/` | ToolSpec、schema、注册和通用调用校验、证据与持久化辅助 |
| `runtime/` | 工具组配置、装配和资源生命周期 |
| `files/` | workspace 文件访问、读取缓存、写入备份与恢复 |
| `database/` | 有界 SQLite 查询 |
| `execution/` | 命令与 Python 执行、超时、输出限制 |
| `web/` | 页面抓取与可选 Playwright 浏览器 |
| `retrieval/` | 将 RAG 服务适配成 retrieve 工具 |
| `skills/` | 索引 skill metadata，按需加载正文 |
| `mcp/` | 多 server 连接管理，stdio / Streamable HTTP 客户端 |
| `delegation/` | 子任务契约、只读 worker、proposal 与主 agent 应用 |
| `history/` | 当前 session 原始历史的字面搜索、分页读取与来源 ID |
| `tasks/` | 持久任务计划、验收、检查点接口 |

添加工具时定义 ToolSpec 与 JSON Schema，并在统一 loader 中接线；read/write/execute 分类必须反映实际副作用。Runner 负责权限、预算、并发调度和事件，工具处理函数专注具体操作。工具失败抛出 ToolError 或返回明确失败观察，不能吞掉异常伪装成功。

read_file 使用有界 LRU，每次仍校验访问范围和元数据；`limits.tools.read_cache_bytes=0` 关闭。缓存不保存权限决定、命令或写入结果。

相关说明：[架构](../../../docs/architecture.md)、[配置](../../../config/README.md)、[MCP](mcp/README.md)、[RAG](../rag/README.md)。
