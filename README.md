# ajAgent

一个通过逐阶段实现来学习 agent 与 agent harness 的项目。使用 DeepSeek / OpenAI GPT，自行实现主循环、工具执行、上下文管理和任务协调。

当前已完成 **Stage 0 和 Stage 1**：uv 环境、配置模板，以及自行实现的 DeepSeek 最小工具调用循环。真实模型演示与 21 项离线测试已通过。

- [阶段 TODO 与验收标准](docs/TODO.md)
- [架构设计与主要流程图](docs/architecture.md)
- [学习日志](docs/learning-log.md)

建议按 Stage 0 → 1 → 2 → 3 → 4 → 5 → 6 → 7 推进；Stage 8、9 为后续选修。TODO 中后续阶段的目录是未来实现目标。

## 环境

使用 uv 管理依赖和项目内的 `.venv`。当前 `.python-version` 选择 Python 3.13，项目声明支持 Python 3.11+；本机已验证 Python 3.13.7。

在项目根目录执行：

```bash
uv sync --locked
uv run python --version
uv run python -c "import httpx, pytest, tomllib; print('Environment ready')"
```

无需手动激活虚拟环境，后续命令使用 `uv run` 即可。依赖版本由 `uv.lock` 锁定；uv 缓存放在被忽略的 `.uv-cache/`。当前只安装 HTTP 客户端 `httpx` 和开发测试工具 `pytest`，没有 Agent SDK。

## 本地配置

本机已生成 [config.local.toml](config.local.toml)。直接在对应小节填写：

- `[deepseek]` 的 `api_key` 和 `model`：Stage 1 使用。
- `[openai]` 的 `api_key` 和 `model`：Stage 2 使用；目前可以留空。
- `[embedding]` 的 `model`：Stage 3 再配置，复用 OpenAI 凭据。

模型 ID 留空，避免提前绑定不适用于账户的型号。`[runtime]` 默认选择 `deepseek`、工作目录 `.`、最多 8 步、请求超时 30 秒。Stage 1 已读取并校验 DeepSeek 配置；只有运行 agent 命令才会发送 API 请求。workspace 预留给 Stage 2 文件工具，当前不会读取工作目录内容。

新克隆的仓库不包含本地配置，只需首次执行（不要覆盖已填好的配置）：

```bash
cp -n config.example.toml config.local.toml
chmod 600 config.local.toml
git check-ignore config.local.toml
```

`config.local.toml`、`.env`、`.venv/`、`.uv-cache/` 和 `.ajagent/` 已加入 `.gitignore`。真实 key 只填本地文件或环境变量，不填示例模板；忽略规则不会移除已经提交的文件或历史。

## 配置加载

优先级：CLI 非秘密参数 > 环境变量 > `config.local.toml` > 默认值。不会提供明文 key 的 CLI 参数。

| 配置 | 环境变量约定 |
| --- | --- |
| DeepSeek key / model | `DEEPSEEK_API_KEY` / `DEEPSEEK_MODEL` |
| OpenAI key / model | `OPENAI_API_KEY` / `OPENAI_MODEL` |
| 运行 provider / workspace | `AJAGENT_PROVIDER` / `AJAGENT_WORKSPACE` |
| 步数 / 超时（秒） | `AJAGENT_MAX_STEPS` / `AJAGENT_TIMEOUT` |

Stage 1 实现 DeepSeek key/model、运行 provider、步数和超时的环境变量覆盖；OpenAI 与 workspace 变量在后续阶段接入。CLI 支持 `--config`、`--model`、`--max-steps`、`--timeout`；目前只接受 DeepSeek + chat_completions。

endpoint 和 api_mode 保留在各厂商配置段。日志打印 provider/model、工具名称及演示结果、终止状态，不打印完整配置、认证头或 key。

## 运行 Stage 1

在项目根目录执行（会调用已配置的 DeepSeek API）：

```bash
uv run python examples/minimal_agent.py "法国的首都是哪里？"
uv run python examples/minimal_agent.py "请先用 lookup_note 查询 study 笔记，再用 add 计算阅读与练习一共需要多少分钟，简洁回答。"
```

第二条命令的真实演示（`deepseek-flash`）执行了 `lookup_note` → `add`，返回 **60 分钟**。内置笔记包含阅读 25 分钟、练习 35 分钟；它是内存中的教学资料，不是 RAG 或持久记忆。

```bash
# Stop after one model request to observe the unfinished state.
uv run python examples/minimal_agent.py "查询 study 笔记并计算总时长" --max-steps 1
# Run offline tests without API credentials or network requests.
uv run pytest -q
```

`max_steps` 计数模型请求轮数，同一轮里的多个工具按顺序执行并逐一回填 ID。只有 `completed` 返回退出码 0；`step_limit`、`empty_output`、`refused`、`incomplete`、API/网络错误及取消均返回 1。API/网络错误不自动重试，超时是单次 HTTP 操作的等待上限，不是整个任务的总时限。

实现使用非流式、关闭 thinking 的请求。assistant 消息原样保留以续接工具结果，但不打印 reasoning 内容。参考 [DeepSeek Tool Calls](https://api-docs.deepseek.com/guides/tool_calls/) 与 [Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)。

## 阅读代码与下一步

先看 [run_agent](examples/minimal_agent.py)，顺着 messages → HTTP 请求 → tool_calls → execute_tool → tool result → 下一轮阅读，再看配置与 CLI。脚本共 122 行非空代码（包含注释），没有隐藏在库中的 agent 循环。

下一阶段将提取统一配置、双厂商适配和可靠工具层；当前保留两种简单工具，不提前加入 RAG、持久状态或多 agent。
