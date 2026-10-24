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

## Stage 8 长任务工具

`tasks` 工具组统一加载 plan_tasks、task_status、start_task、update_task_paths、checkpoint_task 和 verify_task，业务状态在 `ajagent/tasks/`。子 agent 默认不获得这些工具；主 agent 动态委派分析与草稿并负责验收。execution 增加 restore_file；write_file/apply_proposal 经过 files/history.py 保存备份和 diff，回滚受哈希冲突检查约束。

工具参数错误现在返回字段与 schema 约束，不回显参数值。长命令输出保留头尾，避免 unittest 失败摘要被头部日志挤掉；超长结果的 artifact 预览也为命令保留尾部。被模型输出上限截断的报告保留 partial 文本，不执行截断工具调用。

## Stage 8 长任务与执行预算

tasks 分片控制可恢复计划、检查点上下文切换（compact_on_checkpoint）、目标数量与长时间未更新的提醒（max_idle_steps）。默认工具组含 tasks；固定 coordination workflow 默认关闭。恢复同一 task 时读取状态和文件哈希，pending 副作用的检查沿用上一 run。

runtime/harness 模板和本地配置已调整到：100 steps、300 次工具调用、200 万 token、1800 秒；单请求输出 8192 token；工具超时 30 秒。capabilities 子任务限额为 16 steps / 30 calls / 100000 token / 300 秒，并计入父预算。artifact_read_chars=8000，工具输出上限=12000，以减少反复分页。API request timeout=60 秒。

[harness].auto_approve_tools 默认只包含 plan_tasks、update_task_paths、start_task、checkpoint_task、verify_task。真正的 run_command / write_file 等仍走权限，deny 优先。示例编码试验使用单独的 ignored config/long-task-demo.local.toml，workspace 限定为 examples/projects/expense_tracker，并显式授权该试验需要的副作用；不更改主配置的执行授权。

交互审批可输入 `once`（仅本次）、`session`（当前会话内相同权限范围）、`allow_all`（当前会话所有工具）或 `no`（拒绝）。选择 `allow_all` 后，后续 write/execute 不再逐个询问；授权随会话保存，`--resume` 时继续生效，新会话不继承。显式 deny、workspace 限制、hooks 和执行预算仍然有效。

预算软阶段统一位于 `[harness]`：`low_budget_ratio=0.35`、`finalizing_ratio=0.10`、`final_input_tokens=4096`、`final_output_tokens=1024`、`repeat_window=12`。硬上限仍为 max_steps / max_tool_calls / max_tokens / max_seconds；收尾也计费，详见 [Budget Manager](budget-management.md)。

Snake 日志反馈后，默认长任务额度为 300 steps / 900 tools / 6,000,000 tokens / 3,600 seconds；`final_step_reserve=1` 只预留最后一步，steps 不再套用 10% 收尾比例。CLI 打印实际配置，日志中的 `phase_reason` 区分触发资源。详见 [Snake 诊断](experiments/snake-budget-diagnosis.md)。
