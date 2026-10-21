# ajAgent：学习路线与实施 TODO

资料初次核对日期：2026-10-11；RAG 重构：2026-10-17。状态：Stage 0–4 已实现；Stage 3 新版通过分层测试与真实冒烟验证，当前仅保留最新实现与验证文档。Stage 4 已实现并完成离线验证与双厂商会话恢复冒烟，Stage 5 起尚未开始。

## 1. 项目目标与约定

做一个能读取项目与资料、检索证据、调用工具、保存会话、完成研究与写作的本地 CLI agent。先理解单 agent，再搭建简洁的 harness，最后验证多 agent 是否确实带来收益。

贯穿场景：**阅读一个小型代码仓库和一组技术资料，回答带来源的问题，生成技术说明，并根据审阅意见修改。** 不同阶段扩展同一个场景，避免不断换题。

默认技术选择是本项目的设计建议，不是 Claude Code 的技术栈复刻：

- Python 3.11+，标准库优先；CLI 先用 `argparse`，数据结构先用 `dataclass`。
- 允许 `openai` 等基础模型客户端，也可以用 `httpx`；模型客户端只负责请求。循环、工具、状态、检索、记忆和协调由自己实现。
- 不以 OpenAI Agents SDK、Claude Agent SDK、LangChain、LangGraph 等作为主流程。以后可用同一评测集比较。
- 依赖按需增加：Stage 2 参数校验，Stage 3 NumPy/HNSW/Tree-sitter 与可选本地 Qwen，Stage 6 Playwright；不提前引入向量数据库、服务端、队列或容器集群。
- 代码标识符、简短注释和 docstring 使用英文；学习文档可用中文。注释解释约束、原因或协议细节，不逐行翻译代码。
- 生成模型只使用 DeepSeek 与 OpenAI GPT；embedding/reranker 使用本地 Qwen 模型。
- 路线以完成验收为准，不按日历硬赶。每阶段留下一个可重现 demo、关键失败用例和简短学习记录。
- 安全只做与功能直接相关的基本边界：密钥不入库/上下文、工具限制工作目录、执行有超时与上限。复杂企业权限体系不在目标内。

### 阶段总览

| 阶段 | 交付结果 | 建议学习投入 |
| --- | --- | --- |
| 0 | 项目约定、配置、文档基础 | 半天 |
| 1 | 50–150 行最小 agent loop | 1–2 天 |
| 2 | 双厂商适配与可靠工具层 | 2–4 天 |
| 3 | 可核验引用的 RAG | 3–5 天 |
| 4 | 上下文、会话、长期记忆 | 2–4 天 |
| 5 | 可观测、可中断、可恢复的 harness | 4–7 天 |
| 6 | 外部工具、skills、子任务 | 3–5 天 |
| 7 | research → write → review → revise | 3–5 天 |
| 8（选修） | 长任务与反馈改进 | 按需 |
| 9（选修） | 系统评测与性能优化 | 按需 |

上述时间只是个人学习量估计。Stage 5 是单 agent 的完整里程碑；Stage 7 是本路线的核心终点。评测从 Stage 1 就开始，Stage 9 只是深化。

## 2. Claude Code 参考依据与边界

### 文档描述 → 本项目设计

以下链接是已查阅的官方资料。右列均为 **ajAgent 自己的简化方案**，不是对 Claude Code 内部源码的逐行复现。

| 参考资料 | 借鉴内容 | 本项目落点 |
| --- | --- | --- |
| [How Claude Code works](https://code.claude.com/docs/en/how-claude-code-works) | 收集上下文、行动、验证的循环；harness 包围模型 | Stage 1 的循环、Stage 5 的验证反馈 |
| [Memory](https://code.claude.com/docs/en/memory) | 项目指令与跨会话记忆有不同用途 | Stage 4 将 instructions、session、memory 分开 |
| [Permissions](https://code.claude.com/docs/en/permissions) | 工具执行权限由运行时控制 | Stage 5 实现 allow / ask / deny，不复制全部模式 |
| [Hooks](https://code.claude.com/docs/en/hooks) | 生命周期事件连接确定性处理 | Stage 5 使用小型 Python hook 列表 |
| [Skills](https://code.claude.com/docs/en/skills) | 按需加载工作流指令 | Stage 6 先索引描述，再加载正文 |
| [Subagents](https://code.claude.com/docs/en/sub-agents) | 独立上下文、指定工具和角色 | Stage 6 实现单层委派，Stage 7 再协调角色 |
| [MCP](https://code.claude.com/docs/en/mcp) | 外部工具协议接入 | Stage 6 作为可选适配器，不替代内部工具接口 |
| [Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) | 跨会话进度记录、可验证的增量工作 | Stage 8 的任务清单、交接摘要和恢复 |

### 实际阅读的公开代码

仓库：[anthropics/claude-code](https://github.com/anthropics/claude-code)。本次核对版本为 [`2301018b1f61073c501a8e7a4813ef48c239163b`](https://github.com/anthropics/claude-code/commit/2301018b1f61073c501a8e7a4813ef48c239163b)。以下使用固定版本链接，便于以后对照。

| 文件 | 代码观察 | 学习任务 |
| --- | --- | --- |
| [hookify/core/rule_engine.py](https://github.com/anthropics/claude-code/blob/2301018b1f61073c501a8e7a4813ef48c239163b/plugins/hookify/core/rule_engine.py) | 匹配规则后聚合结果，阻断规则优先于提示规则 | Stage 5 理解“事件输入 → 策略结果”，自己用少量函数实现 |
| [ralph-wiggum/hooks/stop-hook.sh](https://github.com/anthropics/claude-code/blob/2301018b1f61073c501a8e7a4813ef48c239163b/plugins/ralph-wiggum/hooks/stop-hook.sh) | 读取循环状态和 transcript，检查迭代上限与完成标记 | Stage 5/8 学习有界反馈循环；本项目完成判断还需要实际验证证据 |
| [pr-review-toolkit/agents/code-reviewer.md](https://github.com/anthropics/claude-code/blob/2301018b1f61073c501a8e7a4813ef48c239163b/plugins/pr-review-toolkit/agents/code-reviewer.md) | frontmatter 定义角色，正文约束审阅范围与输出 | Stage 7 用角色配置和输出契约定义 reviewer |
| [mods/README.md](https://github.com/anthropics/claude-code/blob/2301018b1f61073c501a8e7a4813ef48c239163b/mods/README.md) | 介绍内置 mod、事件注册及测试方式 | 理解扩展与引擎之间的接口；不照搬完整 mod 引擎 |

这些公开材料足以研究扩展边界和部分具体实现，但**不能把插件中的循环等同于 Claude Code 的核心推理循环，也不能据此声称已审阅完整核心实现**。本项目不依赖非官方泄露代码或逆向源码；协议字段以实施时官方文档为准。

### 模型接口资料

- [OpenAI Function calling](https://developers.openai.com/api/docs/guides/function-calling)：工具请求由应用执行，并用调用 ID 返回结果；适配器必须处理多次工具调用。当前文档还明确部分新 GPT 模型使用 Responses API 进行工具调用，不能统一假设所有模型都支持 Chat Completions。
- [DeepSeek API](https://api-docs.deepseek.com/)：官方提供兼容格式接口；实际使用的模型、thinking 设置和工具调用续接字段在每阶段接入时核验。不把“兼容”视作所有字段、模型能力完全一致。

## 3. Stage 0 — 学习工程基础

**目标：** 确定怎样配置、运行和记录学习，不提前搭建框架。

- [x] S0.1 建立 `pyproject.toml`、`.python-version` 与 `uv.lock`；使用 uv 管理 Python 3.13 环境，基础依赖为 httpx，开发依赖为 pytest。
- [x] S0.2 建立 `config.example.toml`（空 key/model）与忽略的 `config.local.toml`，等待用户填写。
- [x] S0.3 在创建真实配置之前，将 `config.local.toml`、`.env`、`.venv/`、`.ajagent/`、Python 缓存加入 `.gitignore`；明确忽略文件不会清除已经提交的秘密。
- [x] S0.4 约定配置优先级：CLI 的非秘密参数 > 环境变量 > 本地配置 > 默认值；不提供 CLI 明文 key 参数。
- [x] S0.5 非秘密配置包括 provider、model、api_mode、workspace、max_steps、timeout；密钥只存在本地配置或环境变量中。
- [x] S0.6 两个厂商的 endpoint 与密钥分别绑定。日志只打印 provider/model，不打印完整配置、认证头或 key。
- [x] S0.7 保持 README 包含安装、配置、运行示例、当前阶段；维护 architecture，文档开头必须有主要流程图。
- [x] S0.8 建立学习日志；每阶段写下“学到了什么、失败在哪里、为什么这样拆分”。

**文件安排（已建立）：**

```text
ajAgent/
├── README.md
├── pyproject.toml
├── uv.lock
├── .python-version
├── .gitignore
├── config.example.toml
├── config.local.toml          # Local only; never commit.
└── docs/
    ├── TODO.md
    ├── architecture.md
    └── learning-log.md
```

**验收：** `uv sync --locked` 成功，环境依赖可导入，TOML 可解析，`git check-ignore config.local.toml` 命中；示例配置不含真实 key。本阶段只建立环境、模板与配置约定；配置优先级和日志规则将在 Stage 1/2 的运行代码中实现，Stage 0 未调用模型 API；Stage 1 已完成真实调用验证。

## 4. Stage 1 — Build a Minimal Agent Loop

**目标：** 用一眼能读完的程序理解模型如何选择工具。先选 DeepSeek 的工具调用接口完成闭环，GPT 在 Stage 2 接入。

- [x] S1.1 编写 `examples/minimal_agent.py`，50–150 行非空代码（含必要注释），覆盖配置读取、工具定义、模型请求、执行、回填、终止；不能通过把循环藏到库里达到行数目标。
- [x] S1.2 只提供两个简单工具，例如 `add(a, b)` 与 `lookup_note(topic)`；后者读取程序内演示资料，不依赖网络。
- [x] S1.3 显式维护 messages；保存 assistant 工具请求，再为每个调用补回对应 ID 的结果。初期顺序执行一轮中的多个工具。
- [x] S1.4 使用 JSON 参数解析和明确的工具映射，不能 `eval` 模型生成内容。
- [x] S1.5 区分正常最终答案、工具请求、模型拒绝/空输出、API 失败、步数耗尽；不能把步数耗尽包装成成功。
- [x] S1.6 设置 `max_steps=8` 和请求超时；未知工具、参数错误变成工具反馈；网络错误先清楚报错，不做复杂恢复。
- [x] S1.7 打印简短过程：工具名称、结果摘要、最终答案。不要求模型输出隐藏推理过程。
- [x] S1.8 用受控假响应验证工具闭环，再手工运行一次真实模型演示；记录模型 ID 和结果。

**文件安排：**

```text
examples/
└── minimal_agent.py
tests/
└── test_minimal_loop.py       # Scripted model outputs; no API key.
```

**验收场景：** 直接回答一个常识问题；查找演示笔记并做一次计算；错误工具名收到错误反馈；连续工具调用在第 8 步终止。能手动指出每条 tool result 对应哪个调用。

**实现记录（2026-10-11）：** `examples/minimal_agent.py` 为 122 行非空代码；`uv run pytest -q` 共 21 项通过。真实 `deepseek-flash` 演示依次调用 lookup_note、add，返回 60 分钟，状态 completed。配置按 CLI > 环境变量 > 本地文件 > 默认值加载；使用非流式、非 thinking 请求，没有自动重试。

**暂不做：** RAG、会话恢复、通用 registry、流式、多 agent。保留这个独立教学示例，后续不要把它变成大型入口。

## 5. Stage 2 — Model Adapters and Reliable Tools

**目标：** 将最小循环迁入包，支持 DeepSeek / GPT，工具错误不再直接摧毁整个任务。

- [x] S2.1 从 Stage 1 提取 `core/loop.py` 与数据结构；保留顺序执行，用函数和小 dataclass，不建设通用工作流框架。
- [x] S2.2 定义 `ModelClient.generate(context, tools) -> ModelTurn`；输出统一为 text、tool_calls、usage、stop_reason，同时保留协议续接必需的原始 items。
- [x] S2.3 实现 DeepSeek Chat Completions 与 OpenAI Responses 两个适配器；各自映射 schema、调用 ID、工具结果和续接状态。不要通过只改 base_url 来宣称支持所有 GPT。
- [x] S2.4 正确保留所选接口要求回传的 reasoning/opaque items，不解析成业务逻辑，也不展示为解释。对不支持的参数明确报错；不默默吞掉工具调用。
- [x] S2.5 用配置选择模型 ID，不硬编码“当前最好”或价格；初始不自动切模型、不在同一会话中途切 provider。
- [x] S2.6 定义 ToolSpec（name、description、JSON Schema、handler）和 ToolResult（ok、data、error、sources）；注册表先是普通字典。
- [x] S2.7 实现 `read_file`、`list_files`、`search_files`（可封装 rg）、只读 `query_sqlite`；SQL 使用演示库的只读连接并限制行数。
- [x] S2.8 参数校验、文件不存在、空搜索、SQL 错误、超时、过长输出分别给出可理解的结果；保留错误类型与可恢复提示。
- [x] S2.9 每个文件工具限制在指定 workspace，排除秘密配置与 `.ajagent/`；限制单次结果长度。简单路径解析即可，不做复杂策略语言。
- [x] S2.10 API 限流/临时服务错误最多重试两次并退避；认证与无效参数立即失败。模型请求重试不重放已经执行的工具副作用。
- [x] S2.11 用规范化参数识别连续重复的工具调用；无新信息时提示换策略，连续三次无进展则停止。先不做全局缓存。
- [x] S2.12 测试“单轮多个调用全部回填”“一个工具失败其他工具仍返回”“空结果不是成功证据”；两家各做一次可选真实 smoke test。

**文件安排（新增/重构，其余沿用）：**

```text
src/ajagent/
├── __init__.py
├── cli.py              # 程序入口
├── config.py
├── core/               # 主流程相关内容
│   ├── types.py
│   └── loop.py
├── models/             # 模型适配器
│   ├── base.py
│   ├── deepseek.py
│   └── openai.py
└── tools/              # 工具定义与执行：registry负责注册所有tools
    ├── base.py
    ├── registry.py
    ├── files.py
    └── database.py
tests/
├── test_stage2.py           # Adapter, loop, config, and tool checks.
└── fixtures/                 # Synthetic API responses and a tiny workspace.
```

**验收结果（2026-10-11）：** 54 项离线测试通过（含 Stage 1）。同一任务在 deepseek-flash / gpt-6-luna 上均执行 search_files → read_file，并回答 60 分钟、引用 plan.md 第 4 行。新增 src 包和 ajagent CLI；搜索首版用 Python 字面量扫描，没有依赖 rg。无进展检测按连续相同批次实现；超时为文件扫描/SQL 合作式限制与 HTTP 操作超时，尚无全任务硬时限。

## 6. Stage 3 — Optional Layered Retrieval

**目标：** RAG 为普通可选工具，模型自主决定是否调用。按 Local → Semantic → Web 分层实现与验证，再接入统一融合/rerank。

- [x] S3.1 `--rag` / TOML 开关；默认关闭，基础工具保持可用，不要求每次回答检索。
- [x] S3.2 Local：rg 字面量/正则、Tree-sitter Python/JS/TS/TSX 符号搜索、文件过滤与行号。
- [x] S3.3 Semantic：章节/AST 分块，PDF 一次提取，独立正文缓存。
- [x] S3.4 本地 Qwen3-Embedding-0.6B，固定模型版本、query instruction、归一化。
- [x] S3.5 vectors.npy + metadata.sqlite + HNSW ANN；文件增删改、内容向量复用、过期结果过滤。
- [x] S3.6 generation 快照原子发布；失败保留旧索引；writer 文件锁。
- [x] S3.7 Web：Tavily SDK 适配、URL 去重、摘录来源标记、单次请求与直接错误返回。
- [x] S3.8 统一 Hit/SearchResult，RRF 融合，Qwen3-Reranker-0.6B 精排。
- [x] S3.9 统一 retrieve 工具；完整证据预算裁剪、部分后端失败时保留其余结果。
- [x] S3.10 分层离线测试；真实 Qwen embedding/rerank、Tavily、DeepSeek 检索与 GPT 无工具回答验证。
- [x] S3.13 tools 按文件/数据库/检索分组；支持独立 Local Search 路径、外部知识库源/输出目录、仅凭索引位置启动 agent。
- [x] S3.11 rag/README 详细说明；仅保留当前实现、测试与验证记录。
- [ ] 后续：新版大语料 recall/延迟评测、引用语义支持评测、compaction/GC、更多文档类型；不作为本次核心功能完成条件。

```text
src/ajagent/rag/
├── README.md
├── types.py / service.py / cli.py
├── local/       # rg and Tree-sitter
├── semantic/    # chunking, Qwen, snapshots and HNSW
├── web/         # search snippets and cache
├── ranking/     # Qwen reranker
src/ajagent/tools/retrieval/search.py
tests/rag/
knowledge/.index/            # Generated data; ignored.
```

验收与限制见 [新版验证](experiments/rag-validation.md) 和 [RAG 说明](../src/ajagent/rag/README.md)。新版不强制最终引用校验；来源存在不等于支持结论。

## 7. Stage 4 — Context, Session, and Memory

**目标：** 分清本轮给模型看的内容、可恢复历史和跨会话知识，避免“把所有东西塞进 prompt”。

- [x] S4.1 短期上下文：当前任务、最近对话、必要工具结果，只存在本次模型输入中，允许裁剪。
- [x] S4.2 会话历史：完整消息与工具结果写入 JSONL，用 session_id 恢复；新会话默认不加载旧会话历史。
- [x] S4.3 长期记忆：先做显式 `remember/list/forget`，记录文本、来源、更新时间和 scope；用户偏好用可读 Markdown，索引信息用 JSON。暂不自动把每次回答变成记忆。
- [x] S4.4 项目指令：加载工作目录里的 `AJAGENT.md`，和长期记忆分开；首版不实现多级继承或任意文件导入。
- [x] S4.5 构建 context builder，预留输出预算；计数不精确时标记 estimated，超限先截断大工具输出，再压缩旧对话。
- [x] S4.6 压缩摘要保留目标、约束、已完成工作、未解决问题、证据位置；保留最近完整的调用/结果对，不能只留下 tool result。
- [x] S4.7 原始历史不被摘要覆盖；摘要失败时保留原状态并清楚结束，不反复压缩重试。
- [x] S4.8 记忆默认限制条数/长度；明确陈旧记忆不能盖过新文件事实和用户纠正；支持修改和删除。
- [x] S4.9 用两次会话验证恢复与遗忘；同一问题分别关闭/开启记忆，观察答案变化及上下文大小。

**文件安排：**

```text
src/ajagent/context/
├── builder.py
└── compaction.py
src/ajagent/memory/
├── cli.py
├── session.py
└── long_term.py
AJAGENT.md
tests/test_context.py
tests/test_memory.py
.ajagent/
├── sessions/<session_id>.jsonl
└── memory/
    ├── MEMORY.md
    └── metadata.json
```

**验收：** 重启后能续接历史；新 session 不携带旧对话但可加载显式偏好；forget 后下次输入不再含该记忆；压缩后关键约束和引用仍可追溯，协议消息顺序有效。

## 8. Stage 5 — One Modern Agent Harness

**目标：** 在已有 loop 外加入执行管理。harness 管工具、上下文、状态和反馈，模型仍只负责产生下一步请求。

- [ ] S5.1 建立 `Runner` 组合 loop、context、tools、session、policy，不再维护第二份推理循环。
- [ ] S5.2 显式状态：running、waiting_for_user、completed、failed、cancelled；停止原因单独保存（step_limit、budget_limit、no_progress 等）。
- [ ] S5.3 记录 JSONL 事件：model_started/finished、tool_started/finished、permission_requested、compacted、run_finished；携带 run_id、step、call_id、耗时、usage、结果摘要。
- [ ] S5.4 日志与会话分别负责观测和恢复；敏感值不写入二者。大结果保存 artifact，模型只收摘要和读取位置。
- [ ] S5.5 实现最小 allow/ask/deny：workspace 内只读自动允许，写文件/执行默认询问一次后可在本会话授权；拒绝也作为工具结果返回模型。
- [ ] S5.6 添加 `write_file`（显示变更摘要）和受限 `run_command`（参数列表、工作目录、超时、输出限额）。本地子进程不是安全沙箱，演示只运行受控项目命令。
- [ ] S5.7 实现 before_tool、after_tool、before_finish 三个 hook 点；权限判断在真实执行前完成，after_tool 不声称能撤销已发生操作。
- [ ] S5.8 验证 hook：写完文档检查引用，修改演示代码后运行指定测试。失败结果回到 loop，最多修订两轮；模型说“完成”不是唯一完成条件。
- [ ] S5.9 增加 Ctrl-C 取消，终止当前子进程并写入状态；支持在下一次循环开始前接收用户补充，不强求实时 UI。
- [ ] S5.10 保存完成的 step/checkpoint；恢复时不重复已完成的副作用。崩溃在“执行后、落盘前”时标为结果未知，要求核对，不能假装保证 exactly-once。
- [ ] S5.11 统一 step、tool_calls、token 和 wall-clock 上限；价格未知时只报告 token，不伪造费用。
- [ ] S5.12 增加离线 trace 查看和假模型回放；回放不再次执行工具，也不宣称真实模型输出可确定性重现。

**文件安排：**

```text
src/ajagent/harness/
├── runner.py
├── state.py
├── policy.py
├── hooks.py
├── events.py
└── budget.py
src/ajagent/tools/execution.py
tests/test_harness.py
tests/test_resume.py
evals/cases/harness.jsonl
.ajagent/runs/<run_id>/
├── events.jsonl
├── checkpoint.json
└── artifacts/
```

**验收：** 跑通“读取 → 修改演示文件 → 检查 → 修正 → 输出”的任务；拒绝工具后仍能解释剩余工作；中断可恢复；验证失败有上限；恢复不会自动重放结果未知的写操作。trace 能回答“为什么停下、调用了什么、证据在哪”。

## 9. Stage 6 — External Tools, Skills, and Delegation

**目标：** 扩展能力但保持同一工具入口，理解按需上下文和隔离子任务。

- [ ] S6.1 Web：实现 `fetch_url`（超时、正文提取、来源 URL），与真正的 `web_search` 分开。搜索已在 Stage 3 使用 Tavily；本阶段补充全文抓取，使用离线 fixture 验证。
- [ ] S6.2 Browser：用 Playwright 操作本地演示网页，暴露 navigate/read/click，至少完成一次点击后读取动态内容；浏览器可选安装，未安装时工具不注册。
- [ ] S6.3 Code：扩展受控 Python 脚本执行，返回 stdout/stderr/exit_code；不在主进程里 eval。结合 Stage 5 权限和超时，后续需要强隔离时再加容器。
- [ ] S6.4 工具结果统一来源结构，使文件、数据库、网页、浏览器输出都能进入 evidence map；抓取失败不拿搜索摘要当原文。
- [ ] S6.5 Skill：读取 `skills/*/SKILL.md` 的 name/description，首版手动 `/skill name` 加载正文，后续才支持模型选择；正文只是指导，不越过工具权限。
- [ ] S6.6 子任务：添加 `delegate(task, role)`，子 agent 复用 Runner，独立 messages、工具白名单和预算，返回 summary、evidence、artifacts、status。
- [ ] S6.7 父级只收到任务产物和摘要，不拼接全部子 agent 历史；深度限制为一层，子任务消耗计入父级总预算。
- [ ] S6.8 可选 MCP：接一个本地 stdio 示例服务，完成 list_tools/call_tool → 内部 ToolSpec/ToolResult 映射；可使用协议客户端 SDK，但不用其 agent 编排。

**文件安排：**

```text
src/ajagent/tools/
├── web.py
├── browser.py
└── delegation.py
src/ajagent/extensions/
├── skills.py
└── mcp.py                    # Optional.
skills/research/SKILL.md
examples/browser_fixture/index.html
examples/scripts/analyze_data.py
tests/test_extensions.py
tests/test_delegation.py
```

**验收：** 搜索后抓取原文并引用；浏览器完成一次本地交互；脚本能超时终止；skill 正文按需加载；子任务失败不会吞掉父任务，不能无限递归。MCP 可单独延期，不阻塞主线。

## 10. Stage 7 — Multi-Agent Is Coordination

**目标：** 实现可解释的小型协作系统，并比较是否值得多花请求和 token。

- [ ] S7.1 先做确定性、顺序的 coordinator：research → write → review → revise；每个节点是独立角色配置的 Runner。
- [ ] S7.2 Researcher 产出事实、证据 ID、未解决问题；writer 产出草稿和引用；reviewer 产出带定位的 issues 与 pass/fail；reviser 根据 issues 改稿。
- [ ] S7.3 Coordinator 负责路由和状态，不让角色自行宣告整个任务成功。review 通过则结束，失败最多 revise 两轮，仍失败返回草稿及未解决问题。
- [ ] S7.4 定义 Handoff：task_id、goal、inputs、evidence_ids、artifact_paths、acceptance_criteria、remaining_budget。只传必要信息。
- [ ] S7.5 各角色可选择 DeepSeek/GPT，但默认先用相同模型隔离“角色分工”和“模型差异”的影响。
- [ ] S7.6 产物使用独立目录和版本号；只有 coordinator 决定最终输出，不让多个角色同时覆盖同一个文件。
- [ ] S7.7 工具权限按角色裁剪：researcher 读/检索，writer/reviser 写自己的产物，reviewer 读/检查；各自上下文隔离。
- [ ] S7.8 某节点失败记录 failure，最多一次明确重试或返回 partial；不能把空输出当合格 handoff。
- [ ] S7.9 串行稳定后选做两个 researcher 并发，再汇总冲突与重复证据；先设并发数 2，不做 agent swarm。
- [ ] S7.10 用相同资料和 10 个任务比较单 agent 与多 agent；各跑 3 次，记录引用支持、任务完成率、延迟、token 和失败原因。控制总预算，允许结论是“多 agent 没有更好”。

**文件安排：**

```text
src/ajagent/multi_agent/
├── roles.py
├── handoff.py
└── coordinator.py
agents/
├── researcher.md
├── writer.md
├── reviewer.md
└── reviser.md
tests/test_coordinator.py
evals/cases/multi_agent.jsonl
docs/experiments/multi-agent-comparison.md
.ajagent/runs/<run_id>/artifacts/
├── research.json
├── draft-v1.md
├── review-v1.json
└── final.md
```

**验收：** 至少演示一次 review 失败后成功修改；缺证据时不会空转；父级总预算可终止所有节点；最终文档每个引用可追溯。对比报告包含失败样本，不仅展示最好案例。

## 11. Stage 8（选修）— Long-Running Tasks and Feedback

**进入条件：** Stage 5 恢复可靠，Stage 7 能给出明确终止结果。参考前述 Anthropic 长任务文章，但采用本地简单实现。

- [ ] S8.1 将大任务拆成可验证 feature/task 列表，保存 pending/running/passed/failed 与验收证据。
- [ ] S8.2 每个工作段只处理一个小目标，段末保存 progress.md、产物和下一步；新工作段读取事实再继续。
- [ ] S8.3 增加用户反馈入口，把纠正转换为待审核记忆候选；不自动把模型反思视作正确知识。
- [ ] S8.4 在演示仓库中为 agent 文件写入保存简单备份/diff；明确文件回滚不等于撤销数据库或外部操作。
- [ ] S8.5 模拟进程退出后继续，核对已验证任务不重复执行，未验证任务不会被标完成。

```text
src/ajagent/tasks/
├── plan.py
├── progress.py
└── feedback.py
tests/test_long_running.py
.ajagent/tasks/<task_id>/
├── tasks.json
├── progress.md
└── checkpoints/
```

**验收：** 一个三段任务在第二段中断，恢复后完成剩余工作，最终状态由验证结果决定；不需要常驻服务或自动定时调度。

## 12. Stage 9（选修）— Evaluation and Optimization

- [ ] S9.1 汇总已有 cases，分类统计工具选择、检索、引用、恢复、权限拒绝和协作表现；把失败样本加入回归集。
- [ ] S9.2 实验关闭/开启检索、记忆、压缩、多 agent，保持任务集与预算可比；报告多次运行的波动。
- [ ] S9.3 测量耗时分布和 token，先定位瓶颈，再尝试 embedding 缓存、工具缓存或并行只读调用；明确缓存失效条件。
- [ ] S9.4 加流式输出时只在完整工具参数收到后执行，取消时清理未完成请求；不要执行半段 JSON。
- [ ] S9.5 可选与 Agent SDK 实现对照，放在 `comparisons/`，不替换本项目主流程。
- [ ] S9.6 整理一个“失败 → 诊断 → 改动 → 评测结果”的技术报告，作为项目展示材料。

```text
evals/
├── run.py
├── metrics.py
└── cases/
docs/experiments/ablation.md
comparisons/README.md
.ajagent/eval-results/        # Local reports; sanitized summaries go in docs.
```

**验收：** 每项优化都能对应指标和代价；没有可测收益的复杂机制可以删除。

## 13. 每个阶段的统一完成清单

- [ ] 本阶段核心 TODO 完成，选修项明确标注延期。
- [ ] 有一个正常 demo 和一个失败/边界 demo，记录实际命令与结果。
- [ ] 关键行为有离线测试；真实 API 测试手动触发，不在普通测试中消耗费用。
- [ ] README 更新当前功能和运行方法，不提前宣传未实现能力。
- [ ] architecture 的流程图、模块表与实现一致，标记已实现阶段。
- [ ] 英文注释简洁，函数职责可说明；新抽象有实际使用需求。
- [ ] 配置、日志、测试 fixture 和索引没有真实 key；生成数据没有误入 Git。
- [ ] 学习日志写下本阶段取舍，再开始下一阶段。可以在验收后手动打 `stage-01` 等标签保存学习快照。

目录采用**逐阶段累积**：Stage 2 起主程序始终在 `src/ajagent/` 演进；仅 Stage 1 保留独立教学脚本，不复制多套项目。上面的目录树是模块安排，不要求一个小函数就拆成一个文件；实际过小时可合并，但保持职责边界。

## 14. 下一次开始实现时的任务说明

建议从以下范围开始：

> 按 docs/TODO.md 实现 Stage 4，先阅读 docs/architecture.md 和已完成的 RAG 模块。保留已有示例与双厂商工具循环，新增上下文管理、会话历史与显式长期记忆；不实现后续阶段。代码使用简洁英文注释，配置中不写真实 API key。完成离线验证，更新 README、architecture 和 TODO；真实 API 验证只使用已配置的厂商凭据，未运行的验证如实标注。
