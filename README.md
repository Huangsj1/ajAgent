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

Stage 0–6 已完成。新增网页抓取、可选浏览器、Python 子进程、按需 skill、带 contract 的并行子任务、single-writer 提案与持久 MCP。示例：

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
