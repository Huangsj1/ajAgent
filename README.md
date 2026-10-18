# ajAgent

一个通过逐阶段实现来学习 agent 与 agent harness 的项目。使用 DeepSeek / OpenAI GPT，自行实现主循环、工具执行、上下文管理和任务协调。

当前已完成 **Stage 0–3**：最小循环、双厂商工具层，以及可选的 Local / Semantic / Web 检索工具、Qwen embedding 和 reranker。分层说明与验证见下方文档。

- [阶段 TODO 与验收标准](docs/TODO.md)
- [架构设计与主要流程图](docs/architecture.md)
- [学习日志](docs/learning-log.md)
- [RAG 分层实现与使用](src/ajagent/rag/README.md)
- [新版 RAG 验证](docs/experiments/rag-validation.md)

建议按 Stage 0 → 1 → 2 → 3 → 4 → 5 → 6 → 7 推进；Stage 8、9 为后续选修。TODO 中后续阶段的目录是未来实现目标。

## 环境

使用 uv 管理依赖和项目内的 `.venv`。当前 `.python-version` 选择 Python 3.13，项目声明支持 Python 3.11+；本机已验证 Python 3.13.7。

在项目根目录执行：

```bash
uv sync --locked
uv run python --version
uv run python -c "import httpx, pytest, tomllib; print('Environment ready')"
```

无需手动激活虚拟环境，后续命令使用 `uv run` 即可。依赖版本由 `uv.lock` 锁定；uv 缓存放在被忽略的 `.uv-cache/`。基础依赖还包括 Tree-sitter、HNSW、pypdf 和 DDGS；本地 Qwen 模型通过 `local-models` extra 安装 torch/transformers，测试使用 pytest，没有 Agent SDK。项目通过 hatchling 安装为本地包，提供 `uv run ajagent` 和 `uv run ajagent-rag` 入口。

## 本地配置

本机已生成 [config.local.toml](config.local.toml)。直接在对应小节填写：

- `[deepseek]` 的 `api_key` 和 `model`：Stage 1 使用。
- `[openai]` 的 `api_key` 和 `model`：使用 GPT 时填写；只运行 DeepSeek 时可留空。
- `[rag]` 控制可选检索，默认关闭；不添加该小节也可用 `--rag` 开启。

模板模型 ID 留空，用户在本地文件中选择自己的模型。`[runtime]` 默认选择 `deepseek`、工作目录 `.`、最多 8 步、请求超时 30 秒。Stage 2 按 provider 读取对应凭据与模型；只有运行 agent 命令才会发送 API 请求。workspace 限定文件和数据库工具能访问的目录，相对路径从当前工作目录解析。

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

上述覆盖规则均已在 Stage 2 实现。CLI 支持 `--config`、`--provider`、`--model`、`--workspace`、`--max-steps`、`--timeout`；不支持的 provider 参数或 api_mode 会报错，不会自动切换模型。

额外的 runtime 参数：`tool_timeout=5` 秒（`AJAGENT_TOOL_TIMEOUT`），`max_output_chars=6000`（`AJAGENT_MAX_OUTPUT_CHARS`，最少 1000）。旧本地配置无需修改，未填写时使用默认值。

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

## 运行 Stage 2

同一任务切换 provider 即可（每条命令是新会话）：

```bash
uv run ajagent --provider deepseek --workspace tests/fixtures/workspace "先搜索 study，再读取找到的文件，回答学习总时长并标注行号。"
uv run ajagent --provider openai --workspace tests/fixtures/workspace "先搜索 study，再读取找到的文件，回答学习总时长并标注行号。"
uv run ajagent --help
```

已验证 `deepseek-flash` 与 `gpt-6-luna` 均执行 search_files → read_file，回答 60 分钟并引用 `plan.md:4`。

| Provider | 默认 endpoint | api_mode | 续接内容 |
| --- | --- | --- | --- |
| deepseek | `https://api.deepseek.com` | `chat_completions` | assistant 消息及 tool_call_id 对应结果 |
| openai | `https://api.openai.com/v1` | `responses` | 原始 output items 和 function_call_output |

模型 ID 必须与所选接口兼容。DeepSeek 默认关闭 thinking，可在 `[deepseek]` 设置 `thinking="enabled"`；该模式的续接字段在离线测试中验证，真实演示使用默认模式。OpenAI 使用 `store=false`，在内存中保留 reasoning/opaque items，不打印它们。参考 [OpenAI Function calling](https://developers.openai.com/api/docs/guides/function-calling) 与 [Responses migration](https://developers.openai.com/api/docs/guides/migrate-to-responses)。

| 工具 | 参数 | 返回与限制 |
| --- | --- | --- |
| `list_files` | directory（默认 `.`） | 最多 100 个路径，截断有标记 |
| `search_files` | query、directory | 不区分大小写的字面量搜索；最多扫描 1000 个文件、返回 50 条匹配；含路径/行号 |
| `read_file` | path、start_line、max_lines | UTF-8 文本；默认 100 行，最多 200 行；文件上限 1 MB |
| `query_sqlite` | path、query、max_rows | 只读连接 + SQL 操作限制；默认 50 行、最多 100 行 |

文件工具排除隐藏路径（含 `.env`、`.ajagent/`）、`config.local.toml`、本次使用的配置文件及符号/硬链接；路径必须在 workspace 内。非隐藏文件中的内容仍会发送给所选模型，因此演示默认指向无秘密的 fixture 目录。

返回包含 `ok/data/error/sources`，空搜索/空查询返回空数据和空来源，不代表结论成立。总结果超过字符限额时返回带 `truncated` 的预览并移除可能不完整的 sources，模型应缩小范围重新读取。当前 agent 和 RAG 都提供来源位置；当前没有自动强制引用校验。

SQLite 演示可在临时目录生成数据库：

```bash
uv run python - <<'PYTHON'
from pathlib import Path
import sqlite3
import tempfile
workspace = Path(tempfile.mkdtemp(prefix="ajagent-demo-"))
with sqlite3.connect(workspace / "study.db") as connection:
    connection.executescript(Path("tests/fixtures/workspace/demo.sql").read_text())
print(workspace)
PYTHON
# Replace /path/printed/above with the printed directory.
uv run ajagent --workspace /path/printed/above "查询 study.db 中 study 表，汇总学习分钟数。"
```

Stage 2 的 API 临时错误（429、5xx、网络失败）最多重试两次，退避 0.5/1 秒；400、401 等立即失败。重试只发生在 HTTP 层，不重新执行已经完成的工具。文件扫描和 SQL 提供合作式超时，HTTP 超时按网络操作计；当前没有整个任务的硬时限。

连续三轮相同工具批次（规范化参数与结果均相同，忽略调用 ID）以 `no_progress` 停止，第二轮会提示换策略；不同调用仍由 `max_steps` 限制。`completed` 表示模型给出最终文本，不保证任务事实正确。其余状态返回退出码 1；暂无会话持久化或自动恢复。

## 阅读代码与下一步

先看 [run_agent](examples/minimal_agent.py)，顺着 messages → HTTP 请求 → tool_calls → execute_tool → tool result → 下一轮阅读，再看配置与 CLI。脚本保持在 50–150 行非空代码范围内（包含注释），没有隐藏在库中的 agent 循环。

Stage 2 从 [core/loop.py](src/ajagent/core/loop.py) 开始阅读，然后看 models 的协议适配和 tools 的校验/执行。当前测试位于 `tests/test_minimal_loop.py` 与 `tests/test_stage2.py`，使用 MockTransport 和临时文件，不依赖真实 key 或网络。下一阶段将实现短期上下文、会话历史与长期记忆。


## 运行 Stage 3 RAG

默认不开启 RAG；开启后作为普通工具提供给 agent，由模型决定是否调用。

```bash
# Local search: no embedding, no chat API.
uv run ajagent-rag search 'run_agent' --sources local --mode symbol --no-rerank
# Install and use local Qwen models.
uv sync --locked --extra local-models
uv run --extra local-models ajagent-rag index
uv run --extra local-models ajagent-rag search 'Orion v2 的缓存保留多久？' --sources semantic
uv run --extra local-models ajagent --rag --provider deepseek 'Orion v2 的缓存保留多久？请引用依据。'
# Explicit web-only retrieval.
uv run ajagent-rag search 'Qwen3 Embedding' --sources web --no-rerank
# Keep RAG disabled, even if local configuration enables it.
uv run ajagent --no-rag '你好'
```

默认知识目录 `examples/knowledge`，索引 `.ajagent/rag-index`，模型缓存 `.ajagent/models`。新版不调用 OpenAI embedding API；首次使用下载本地 Qwen 权重。DeepSeek/GPT 仍负责回答，需要相应 key。未修改已有 `config.local.toml`。

Local 使用 rg/Tree-sitter；Semantic 使用章节/AST 分块、独立文本缓存、NumPy/SQLite/HNSW 增量索引；Web 使用 DuckDuckGo 搜索摘要。结果经过 RRF 融合和可选 Qwen rerank，提供路径/行号/URL 和明确的失败提示。DuckDuckGo 已通过实际查询验证。

详细配置、存储结构、限制与分层测试见 [rag/README](src/ajagent/rag/README.md)，真实验证见 [报告](docs/experiments/rag-validation.md)。
