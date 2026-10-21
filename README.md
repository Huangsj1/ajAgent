# ajAgent

一个通过逐阶段实现来学习 agent 与 agent harness 的项目。使用 DeepSeek / OpenAI GPT，自行实现主循环、工具执行、上下文管理和任务协调。

当前已完成 **Stage 0–4**：最小循环、双厂商工具层，以及可选的 Local / Semantic / Web 检索工具、Qwen embedding 和 reranker。以及上下文预算、会话恢复和显式长期记忆。分层说明与验证见下方文档。

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

无需手动激活虚拟环境，后续命令使用 `uv run` 即可。依赖版本由 `uv.lock` 锁定；uv 缓存放在被忽略的 `.uv-cache/`。基础依赖还包括 Tree-sitter、HNSW 和 pypdf；Web Search 使用官方 tavily-python SDK；本地 Qwen 模型通过 `local-models` extra 安装 torch/transformers，测试使用 pytest，没有 Agent SDK。项目通过 hatchling 安装为本地包，提供 `uv run ajagent` 和 `uv run ajagent-rag` 入口。

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

默认知识目录 `knowledge`，索引 `knowledge/.index`，模型缓存 `.ajagent/models`。新版不调用 OpenAI embedding API；首次使用下载本地 Qwen 权重。DeepSeek/GPT 仍负责回答，需要相应 key。当前 example/local TOML 已同步为上述默认路径，厂商凭据保持原值。

Local 使用 rg/Tree-sitter；Semantic 使用章节/AST 分块、独立文本缓存、NumPy/SQLite/HNSW 增量索引；Web 使用 Tavily 搜索摘要。结果经过 RRF 融合和可选 Qwen rerank，提供路径/行号/URL 和明确的失败提示。Tavily 已通过实际查询验证。

详细配置、存储结构、限制与分层测试见 [rag/README](src/ajagent/rag/README.md)，真实验证见 [报告](docs/experiments/rag-validation.md)。

## 自选搜索范围与知识库

```bash
# Search an external directory or a single file.
uv run ajagent --rag --rag-sources local --rag-local-path /path/to/folder --no-rag-rerank '查找相关内容'
# Create/update a separate knowledge index.
uv run --extra local-models ajagent-rag index --source /path/to/notes --output /path/to/index
uv run --extra local-models ajagent-rag update --index /path/to/index
# Start the agent with that index; its source need not be in the agent workspace.
uv run --extra local-models ajagent --rag --rag-sources semantic --rag-index /path/to/index '回答问题并引用依据'
```

默认使用根目录 `knowledge/` 放原文，`knowledge/.index/` 放 SQLite、NumPy、HNSW 和正文缓存；生成数据不入 Git，也不会被再次索引。只给索引位置即可读取其记录的源目录；原文仍需可访问以检查更新状态。TOML 相对路径以配置文件所在目录为基准，CLI 路径以命令当前目录为基准。

工具按职责组织：`tools/files/workspace.py` 管理文件工具，`tools/database/sqlite.py` 管理数据库工具，`tools/retrieval/search.py` 适配检索工具；共享定义和调度保留在 `tools/base.py`、`tools/registry.py`。知识库 Python 接口为 `rag.knowledge.update_knowledge()` 与 `open_knowledge()`，详见 [RAG README](src/ajagent/rag/README.md)。

Web Search 使用 Tavily。将 key 放入 config.local.toml 的 `[tavily]` 小节，可配置 `timeout=30`、`search_depth="basic"`、`max_content_chars=2000`；也可用 TAVILY_API_KEY 环境变量覆盖。搜索每次只发一次请求，不自动重试、延迟、冷却或缓存；错误直接返回，详见 [Web Search](src/ajagent/rag/README.md#3-web-search)。两份 TOML 已同步为新结构，已有 key 保留。

## Stage 4：上下文、会话与记忆

每次 `ajagent` 调用默认新建会话并打印 Session ID；用同一个 workspace、provider/model 和 `--resume` 续接。新会话不会加载旧对话。

```bash
uv run ajagent "你好，我是geer"
uv run ajagent --resume <session_id> "我是谁？"

uv run ajagent-memory remember "请在每次回答前面加上一句“杰哥好！”" --source "用户显式偏好"
uv run ajagent-memory list
uv run ajagent-memory remember "解释时先给简短示例" --id <memory_id>
uv run ajagent-memory forget <memory_id>
uv run ajagent --no-memory "解释一下 agent loop"
```

`ajagent-memory --workspace /path/to/project ...` 管理指定项目的记忆，不需要 API key。首版只支持 workspace scope，最多 20 条、每条 1000 字符；新增/修改记录来源和 UTC 更新时间。不自动保存回答为长期记忆。`metadata.json` 是权威记录，`MEMORY.md` 是可读视图，请用 CLI 修改。

项目根目录的 `AJAGENT.md` 每次启动重新加载为项目指令；不递归加载、不支持导入。显式记忆单独注入，并提示模型优先采用当前用户纠正和新文件事实。`--no-memory` 关闭长期记忆注入，不关闭项目指令或会话历史。forget 不会改写旧会话里已经提及该偏好的对话；要验证彻底不再携带旧偏好，请新建会话。

```toml
[runtime]
context_tokens = 32000
output_tokens = 4096
memory = true
```

这三个字段已加入 example/local 配置。CLI 可用 `--context-tokens`、`--output-tokens`、`--memory/--no-memory` 覆盖。上下文计数是 UTF-8 字节启发式估算，明确标为 estimated，包含工具定义；不是厂商精确 token 数。输出预算也传给模型 API，需按所用模型实际窗口调整。

超限时先在输入副本中裁剪大工具结果，再用同一模型对旧批次生成一次摘要，保留最近两个完整批次和最近用户消息。摘要要求包含目标、约束、完成项、未解问题及证据位置。摘要请求也检查预算；输入过大、摘要失败或摘要后仍超限时返回 `context_error`，不循环尝试压缩。摘要调用计入 CLI Usage，模型传输层仍沿用已有网络重试策略。

运行数据放在 workspace 的 `.ajagent/`：`sessions/<id>.jsonl` 保存原始模型输出、完整工具结果、可恢复消息批次、上下文统计、摘要及结束状态；`memory/` 保存显式记忆。裁剪和摘要不覆盖原始记录。恢复时只回放完整批次并复用摘要检查点；中断时未完成的批次保留审计记录但不回放。首版限单进程写一个会话，不提供并发写入、跨厂商会话转换或损坏 JSONL 自动修复。

模块与验证见 [architecture](docs/architecture.md) 和 [Stage 4 验证](docs/experiments/context-memory-validation.md)。


### 用量与会话写入

`RunResult.usage` / `Total usage (session)` 是当前 session 的累计 input/output tokens。恢复时读取最近一次 end.usage 快照，再加本次主模型和摘要消耗；不能把历次 end 快照求和。新 session 从零开始；不传 session 的程序调用只累计本次运行。`RunResult.last_usage` / `Last model request usage` 是最后一次主模型响应报告的 input/output tokens，不是累计值，也不被摘要请求覆盖。若最后请求失败，则保留此前最后一次已报告值；尚无用量报告时为 null。`Context (estimated)` 仍是输入预算的启发式估算。最后请求的 input 是发送给模型的上下文规模，output 是该次生成量；两者不等于下一轮实际输入，下一轮还可能新增消息或进行裁剪。

会话事件先序列化到内存缓冲，在完整用户消息/模型与工具批次结束时合并写入；累计约 64 KiB 时提前刷新，run_agent 写入结束记录后由 finally 刷新（取消/异常时也会刷新已缓冲事件）。直接使用 Session.append 的调用者需在结束时调用 flush。没有后台线程或定时器，低流量时不会因为每个事件而打开文件。强制杀进程/掉电仍可能丢失未刷新的事件；当前不提供 fsync 级持久化保证。

累计值以最近的 end 为检查点；被中断、尚未生成 end 的调用费用不在恢复累计值中。旧版本 end 保存的是当次运行用量，升级后将最后一个值作为基线，不会自动补算更早费用。
