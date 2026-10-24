# 配置目录

默认入口是 `config/config.local.toml`，`--config` 可以指定其他入口，原来的单文件 TOML 也仍然支持。

首次使用时，在项目根目录运行（不覆盖已有本地文件）：

```sh
for file in config/*.example.toml; do
  local_file="${file%.example.toml}.local.toml"
  if [ ! -e "$local_file" ]; then cp "$file" "$local_file"; fi
done
```

| 文件（均提供 `.example.toml` 模板和被 Git 忽略的 `.local.toml` 本地版） | 职责 |
| --- | --- |
| `config` | `include` 文件列表，无业务配置 |
| `runtime` | 运行参数、上下文、记忆、模型重试 |
| `models` | DeepSeek / OpenAI 模型与 API key |
| `tools` | 工具组开关、skills/MCP 路径、工具限额、委派与并发参数 |
| `rag` | 知识库、索引、embedding/reranker、检索限额和 Tavily key |
| `tasks` | 持久化计划上限、检查点上下文刷新、无进展提醒 |
| `coordination` | 协作默认开关、角色提示词路径、角色模型/工具裁剪、节点重试与修订上限 |
| `harness` | 执行预算、artifact、日志、验证与拒绝列表 |
| `mcp` | 独立的 server 清单，由 MCPManager 按需加载 |

入口模板已引用 local 分片，复制上述全部模板后即可使用；仅复制入口文件会因缺失分片而报错。

加载顺序：按 `include` 顺序递归合并表，然后应用入口本身的值；相同字段后者覆盖前者，数组整体替换。缺失的 include、错误类型或循环引用会报错。省略的业务参数使用代码默认值。MCP 文件不是普通配置分片，不写入 include。

include 路径相对声明它的文件；业务路径（RAG、skills、mcp_config）始终相对最外层入口文件，分片位置不改变含义。例如 `../knowledge` 指项目根目录的知识库。`runtime.workspace` 仍相对命令工作目录，CLI 的路径覆盖保持原语义。MCP server 的 cwd 则相对 MCP 配置文件；测试示例使用 `cwd = ".."`。

所有 `*.local.toml` 都被 Git 忽略并被文件工具排除。只在 local 文件填写凭据，不要填写 example 文件。修改一类配置只需编辑相应 local 分片。

默认保持主 agent 动态委派，coordination.enabled=false。长任务的工具开关在 tools.local.toml，状态配置在 tasks.local.toml，运行预算在 runtime/harness 分片；auto_approve_tools 只自动批准明确列出的工具，deny 始终优先。
