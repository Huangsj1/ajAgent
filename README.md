# ajAgent

一个用于学习 agent 与 harness 的 Python CLI 项目。模型使用 DeepSeek / OpenAI GPT；自行实现模型循环、工具调度、上下文管理、检索、权限与任务恢复，不依赖厂商 Agent SDK。

## 快速开始

在项目根目录安装依赖并复制配置模板；下面的命令不会覆盖已有本地配置：

```sh
uv sync
for file in config/*.example.toml; do
  local_file="${file%.example.toml}.local.toml"
  if [ ! -e "$local_file" ]; then cp "$file" "$local_file"; fi
done
```

在 `config/models.local.toml` 填写模型名称与 API key，在 `config/runtime.local.toml` 选择默认 provider。所有 `*.local.toml` 都被 Git 忽略。

```sh
uv run ajagent '阅读 README.md，解释项目主要功能'
uv run ajagent --provider openai '阅读 docs/architecture.md，总结工具调用流程'

# 启动时打印 Session ID；恢复原会话及其上下文、累计用量。
uv run ajagent --resume SESSION_ID '继续上次任务'

# 操作其他项目；恢复时应使用相同 workspace、provider 和 model。
uv run ajagent --workspace /absolute/path/to/project '分析这个项目的结构'
uv run ajagent --workspace /absolute/path/to/project --resume SESSION_ID '继续'
```

运行记录保存在 workspace 的 `.ajagent/` 中。更换工作目录执行命令时，可用 `--config /absolute/path/to/ajAgent/config/config.local.toml` 指定配置入口。

## 配置与工具权限

配置按用途拆分在 `config/`：模型、运行参数、工具、RAG、预算、任务与协作。文件职责、合并规则和路径规则见 [配置说明](config/README.md)。

**所有工具组统一在 `config/tools.local.toml` 的 `[tools].enabled` 开关。** 默认启用 files、database、execution、web、skills、delegation、artifacts、tasks、history；retrieval、browser、mcp 按需添加。启用工具组不会自动批准写入或执行操作。

审批时可输入 `once`（本次）、`session`（当前授权范围）、`allow_all`（当前会话所有需要询问的操作）或 `no`。显式 deny 始终优先。`allow_all` 随会话保存，恢复后仍有效。

## 知识库与检索

将 Markdown、文本、PDF 或代码文件放进根目录 `knowledge/`；索引可存入其 `.index/`，也可以与原文分别存放在任意外部目录。语义检索和重排使用本地 Qwen 模型，首次使用需要下载权重。

```sh
uv sync --extra local-models
mkdir -p knowledge
# 将自己的资料放入 knowledge/ 后执行：
uv run ajagent-rag index --knowledge knowledge --index knowledge/.index

# 增删改原文后更新；索引会记住原文目录。
uv run ajagent-rag update --index knowledge/.index
uv run ajagent-rag search '你的问题' --sources semantic --index knowledge/.index --no-rerank

# 外部知识库也可以独立构建。
uv run ajagent-rag index --knowledge /absolute/path/to/documents --index /absolute/path/to/index
```

供 agent 使用时，在 `[tools].enabled` 原数组中添加 `"retrieval"`，并设置 `config/rag.local.toml`：

```toml
[rag]
sources = ["local", "semantic"]
knowledge = "../knowledge"
index = "../knowledge/.index"
device = "auto"
rerank = true
```

默认配置入口位于 `config/`，因此这里的 `../knowledge` 指项目根目录。外部索引可直接填写绝对路径。`device="auto"` 使用可用加速设备；CUDA 环境下可利用 NVIDIA GPU。

```sh
uv run ajagent '使用 retrieve 检索知识库，解释相关设计并附来源'
# 本地文本搜索不需要 embedding，也支持项目外目录。
uv run ajagent-rag search '关键词' --sources local --local-path /absolute/path/to/documents --no-rerank
```

RAG 是可选工具，启用后由模型决定是否调用。联网检索在 `rag.sources` 添加 `"web"`，并在同文件 `[tavily]` 填写 API key；使用 Tavily 官方库，失败直接返回错误。分层、索引和引用限制见 [RAG README](src/ajagent/rag/README.md)。

## 复杂任务：规划、查看与恢复

默认主 agent 自行决定是否委派；子 agent 分析、审阅或提出修改方案，主 agent 检查后顺序写入。需要长任务进度时，可以在提示词中明确要求使用 task 工具：

```sh
uv run ajagent '在 examples/projects/snake 创建终端贪吃蛇项目。先使用 plan_tasks 拆分实现、测试和文档，给每项设置验收命令；逐项执行并用 verify_task 验收，阶段结束调用 checkpoint_task。可以委派独立分析和审阅，最终报告未完成项。'
uv run ajagent-task list
uv run ajagent-task show TASK_ID
uv run ajagent --task TASK_ID '根据落盘进度继续，先核对已有产物再完成剩余目标'
```

`--task` 用新会话读取持久进度，`--resume` 延续原会话；二者不是同一个 ID。任务记录位于 `.ajagent/tasks/`。外部项目的查看命令写成 `uv run ajagent-task --workspace /absolute/path/to/project list`。

任务只有实际验收通过才能标记完成，后续文件变动会使旧验收失效。用户纠正可先记录为候选，确认后再进入长期记忆：

```sh
uv run ajagent-task feedback TASK_ID '本项目优先使用 unittest'
uv run ajagent-task accept-feedback TASK_ID CANDIDATE_ID
```

更多恢复与文件回滚边界见 [任务说明](src/ajagent/tasks/README.md)。

## 固定 research workflow（Stage 7）

适用于希望显式经历研究、写作、审阅、修订的任务，默认不启用：

```sh
uv run ajagent --workflow research '阅读 README.md 和 docs/architecture.md，生成带来源的设计说明并审阅'
uv run ajagent --workflow single '由主 agent 自行安排工作'
```

角色提示词在 `agents/`，参数在 `config/coordination.local.toml`。所有角色共享预算，协调器统一保存版本；审阅通过才生成 `.ajagent/runs/<run_id>/artifacts/final.md`。固定 workflow 不支持 `--resume` / `--task`，需要恢复的编码任务请使用默认模式。详见 [协作说明](src/ajagent/multi_agent/README.md)。

## Skills、MCP 与浏览器

Skills 放在 `skills/`，启动时提供描述，模型通过 `load_skill` 按需加载正文；也可用 `/skill 名称` 显式指定。

MCP 支持本地 stdio 和远程 Streamable HTTP，多 server 连接在运行期间复用。安装依赖，在 `config/mcp.local.toml` 配置 server，再将 `"mcp"` 加入工具组：

```sh
uv sync --extra mcp
```

配置示例与本地测试服务见 [MCP README](src/ajagent/tools/mcp/README.md)。浏览器交互需启用 `"browser"` 工具组并安装 Chromium：

```sh
uv sync --extra browser
PLAYWRIGHT_BROWSERS_PATH=0 uv run playwright install chromium
```

## 预算、用量与日志

预算统一控制 steps、工具次数、token 和时间。默认上限为 300 steps、900 次工具调用、600 万 token、3600 秒；任一资源先耗尽都可能结束运行。HIGH_BUDGET 用于探索，LOW_BUDGET 提醒集中交付，FINALIZING 禁止工具并尝试最终总结。收尾仍受可用 token 和时间限制。

```sh
uv run ajagent --max-steps 400 --max-seconds 5400 '完成任务并验证结果'
uv run ajagent-trace .ajagent/runs/RUN_ID --replay
```

`total usage` 为当前 session 的累计用量，包含恢复前记录、摘要与子任务；`last_usage` 是主模型最后一次请求的 input/output，可观察当前上下文规模。trace 回放只读取事件，不重新调用模型或工具。恢复会开始新的运行预算，不会清零 session 累计用量。

## 会话记忆与历史召回

上下文接近窗口时，系统将旧消息分成不可变 episode，并保留结构化工作状态和最近完整工具消息。默认在输入预算 80% 准备后台候选，95% 才等待并采纳；只校验候选覆盖段与工作状态，新增尾部不会使其失效。压缩提交后内存仅保留活跃 groups，原文保存在 JSONL；历史召回使用可重建的 SQLite 索引，已压缩摘要不会被反复拿去摘要。

长度至少三个字符的查询优先使用 FTS5 trigram；短查询按页扫描，即使本页没有命中，也需要继续 next_cursor 才能确认搜索完成。

默认启用的 `history` 工具组提供 `history_search` 和 `history_read`，模型可按需找回精确参数、错误信息或先前纠正：

```sh
uv run ajagent --resume SESSION_ID '先用 history_search 找到之前关于保留天数的纠正，再用 history_read 核对原话后继续'
```

参数位于 `config/runtime.local.toml` 的 `[limits.context]`。`speculative=false` 关闭后台压缩，保留 hard 阶段同步压缩。召回只读取当前 session；历史证据可能过时，当前文件仍需重新检查。设计、失败边界与测试见 [架构中的记忆系统](docs/architecture.md#上下文会话和记忆)。

## 长期记忆

```sh
uv run ajagent-memory remember '代码示例优先使用标准库'
uv run ajagent-memory list
uv run ajagent-memory forget MEMORY_ID
uv run ajagent --no-memory '本次不注入长期记忆'
```

项目规则放在 `AJAGENT.md`，会话历史与长期记忆分别管理。

## 阅读与开发

- [TODO：Stage 0–8 学习路线](docs/TODO.md)
- [Architecture：主流程、模块职责与实现边界](docs/architecture.md)
- [最小教学循环](examples/minimal_agent.py)

```sh
uv run pytest -q tests
```

测试限定在 `tests/`，避免将 agent 在 `examples/` 生成的项目混入本项目回归测试。
