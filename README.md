# ajAgent

用于学习 agent loop、工具、RAG、上下文、执行 harness 和单层委派的 Python 项目。主流程手写，不使用厂商 agent SDK。

```sh
uv sync
# 首次使用：按 config/README.md 复制分组配置模板并填写密钥
uv run ajagent '列出当前项目的源文件'
```

配置模型与密钥后可选择 `--provider deepseek` 或 `--provider openai`。本地配置已被 Git 忽略。

- [架构与流程](docs/architecture.md)
- [配置说明](docs/configuration.md)
- [分阶段规划](docs/TODO.md)
- [学习记录](docs/learning-log.md)
- [RAG 实现](src/ajagent/rag/README.md)

统一工具配置与用法见 [Tools README](src/ajagent/tools/README.md)；MCP 持久连接见 [MCP README](src/ajagent/tools/mcp/README.md)。

Stage 0–8 已完成。新增网页抓取、可选浏览器、Python 子进程、按需 skill、带 contract 的并行子任务、single-writer 提案与持久 MCP。示例：

```sh
uv run ajagent '/skill research 阅读 knowledge/11-evidence.md，给出来源'
uv run ajagent '用 delegate 委派 researcher 总结 knowledge/11-evidence.md'
```

完整测试（浏览器安装命令见配置说明）：

```sh
PLAYWRIGHT_BROWSERS_PATH=0 uv run --all-extras pytest -q
```

回归结果见 [工具验证](docs/experiments/tools-validation.md)；DeepSeek/GPT 适配器共用同一工具与调度入口。子任务费用计入父会话累计，最后请求用量单独保留。

所有工具组统一由 `config/tools.local.toml` 的 `[tools].enabled` 选择；不再使用零散的 RAG/browser/MCP CLI 开关。

配置目录与首次初始化见 [config/README.md](config/README.md)，模型密钥位于 `config/models.local.toml`，Tavily 位于 `config/rag.local.toml`。

Stage 7 提供 research → write → review → revise 协调流程：

```sh
uv run ajagent --workflow research '根据 knowledge/05-release-v2.md 说明当前缓存保留期'
```

配置见 `config/coordination.local.toml`，角色提示词在 `agents/`。各节点共享总预算，独立上下文；研究可并行委派，协调器统一保存版本，审阅通过才发布 final.md。详见 [协作实现](src/ajagent/multi_agent/README.md) 和 [60 次真实对比](docs/experiments/multi-agent-comparison.md)。当前小任务样本中多 agent 成本更高、通过率更低，普通任务仍默认使用单 agent。

Stage 8 默认仍由主 agent 动态委派子任务。启用的 tasks 工具保存计划、真实验收证据和交接进度；文件工具保存备份/diff。恢复长任务：

```sh
uv run ajagent-task list
uv run ajagent --task TASK_ID '根据已保存进度继续完成'
```

配置与反馈审核见 [长任务说明](src/ajagent/tasks/README.md)。当前运行模板上限：100 steps / 300 工具调用 / 200 万 token / 30 分钟；检查点会刷新工作上下文以减少重复历史。默认不自动批准命令和工作区写入，专用无人值守配置可通过 harness.auto_approve_tools 明确授权。

真实编码试验与改进见 [Stage 8 验证](docs/experiments/long-task-validation.md)，产物为 [Expense Tracker](examples/projects/expense_tracker/README.md)。

预算管理支持 HIGH_BUDGET → LOW_BUDGET → FINALIZING，低预算集中补齐交付物，收尾禁用工具；实现与真实编码对比见 [Budget Manager](docs/budget-management.md)。
