# 当前学习进度

更新日期：2026-10-22。仅保留当前实现对应的总结，详细计划见 [TODO](TODO.md)。

| 阶段 | 当前成果 | 学习重点 |
| --- | --- | --- |
| Stage 0 | uv、配置模板、本地 key 配置、依赖锁 | 环境与依赖可复现 |
| Stage 1 | `examples/minimal_agent.py` | 模型 → 工具 → 结果回填 → 最终答案 |
| Stage 2 | DeepSeek/GPT 适配、文件/SQLite 工具 | 协议续接、参数校验、失败与重复调用 |
| Stage 4 | context builder、JSONL 会话、显式记忆 CLI、AJAGENT.md | 输入与历史分离、完整协议批次、可恢复摘要 |
| Stage 3 | 可选 Local/Semantic/Tavily 检索、Qwen embedding/rerank | 分层召回、增量 HNSW、证据与排序 |
| Stage 5 | Runner、权限、hooks、预算、事件与恢复 | 模型请求与实际执行分离、验证反馈、未知副作用 |
| Stage 6 | fetch/browser/Python、skills、单层委派、stdio MCP | 来源类型、按需指导、上下文隔离与父子预算 |

RAG 默认关闭；开启后由 agent 决定是否调用。设备 `auto` 优先 CUDA/MPS，最后 CPU；CUDA 使用 FP16。当前执行环境只能实测 CPU，3080 Ti 的性能仍需在对应机器验证。

当前测试共 203 项，覆盖保留的 Stage 1/2 与分层 RAG。删除了旧 RAG 实现及其 21 项测试、旧索引和过期评测数据；当前原文已迁到 knowledge/，索引已在 knowledge/.index/ 重建；模型缓存保留。验证详情见 [RAG 验证](experiments/rag-validation.md)。

Stage 4 已完成短期上下文、会话恢复和显式长期记忆；Stage 5 的 harness 和 Stage 6 的外部工具与委派已完成，下一步是 Stage 7 的多 agent 协调。RAG 后续可继续研究大语料 recall/延迟、索引压缩与清理、可选引用校验。当前不把检索分数当作事实概率，也不声称引用位置就能证明语义支持。

知识库可独立于项目 workspace：`update_knowledge(source, output)` 构建/更新，`open_knowledge(output)` 从索引读取源目录；agent 用 [rag].index 指定索引。Local Search 用 [rag].local_path 单独指定外部目录或文件。tools 按 common/runtime/files/database/retrieval/execution/web/delegation/skills/mcp 功能分组；不再保留 extensions 分类。CLI 路径相对当前目录，TOML 路径相对配置文件所在目录。

Web Search 采用 Tavily Python SDK，认证与参数集中在 `[tavily]`。移除旧搜索依赖和重试/冷却逻辑，保留单次请求、结果归一化与直接错误返回。107 项测试通过，使用本地 key 的 SDK 真实搜索成功返回 3 条结果，无 warnings。

Stage 4 新增 13 项离线测试；DeepSeek 与 OpenAI 均通过真实两轮 JSONL 会话恢复验证。详见 [上下文与记忆验证](experiments/context-memory-validation.md)。

2026-10-21：新增累计/最后请求用量区分、摘要费用只累计一次、会话批次/大小缓冲刷新和中断刷新测试；全套 125 项通过。写盘优化未做性能基准，不报告具体加速比例。

Session total usage 改为跨 resume 累计，最近 end 是累计快照；新增新会话归零、无 end 恢复、连续恢复与同对象复用、错误结束及摘要增量验证，全套 128 项通过。

Stage 5 新增 18 项测试，DeepSeek/GPT 均完成真实读来源、写引用文档任务。详细结果见 [Harness 验证](experiments/harness-validation.md)。

2026-10-22：整理可调默认值与配置传递，合并参数校验、超时控制和结束记录，移除重复状态输出及过时调试注释。新增 21 项配置生效/兼容性回归测试；现有知识库索引只读打开验证通过。详见 [配置说明](configuration.md)。

Stage 6 新增 25 项测试；Chromium 本地动态点击、官方 MCP SDK 的真实 stdio 服务、DeepSeek/GPT 真实子任务均通过。父级 total usage 含子任务，last_usage 保持父级最后请求语义。配置同步更新 example/local TOML，补齐缺失 README 以修复 uv 的 editable build。详见 [Stage 6 验证](experiments/tools-validation.md)。

工具体系重构：以 `[tools]` 和 ToolManager 统一开关、装配与生命周期；移除 extensions 分类。MCP 使用持久多 server session，支持 stdio/Streamable HTTP 与独立配置；skills 支持模型按需加载。Runner 并行调度独立 read，串行处理 write/execute；同一步多个 delegate 通过 contract 隔离任务，子任务仅输出提案，主 agent 审阅并顺序应用。真实 DeepSeek/GPT 同步返回两个 delegate，日志确认任务重叠和父子 token 求和正确。203 项回归通过，详见 [工具系统验证](experiments/tools-validation.md)。

### 配置拆分与 MCP 测试服务整理

配置迁入 `config/`，入口 include 组合 runtime、models、tools、rag、harness 分片；MCP 清单独立按需加载。保留本地凭据与用户参数，调整相对路径，统一 CLI/RAG/最小示例的默认入口。支持原有单文件配置，并增加分片合并、错误 include 与模板初始化回归。

测试服务器移至根目录 `mcp/`：add 为 stdio，state 支持 stdio / Streamable HTTP；测试与手动调试共用服务器脚本。完整回归 206 passed（包含浏览器）；实际本地 MCP 配置调用 add(2, 3) 返回 5。没有调用收费模型或打印凭据。

### Stage 7：协调的代价

实现显式 Handoff、独立角色 Runner、版本化输出和最多两轮 review/revise。与 Stage 6 复用工具调度和分层预算，研究 worker 可并行，writer/reviser 的输出统一由 coordinator 保存。JSON 合同与证据 ID 校验能发现格式与来源错误，但语义正确性仍需审阅，不能被 ID 检查替代。

真实 smoke 暴露了长期问候偏好污染机器可读输出的问题，因此角色只继承项目指令和本次契约，隔离会话偏好。DeepSeek/GPT 完整流程均通过。10 个任务各模式运行 3 次后，多 agent 综合通过 26/30，单 agent 28/30，多 agent 使用约 4.35 倍 token；保留全部失败，不为了展示多 agent 而选择最好样本。小任务默认单 agent，复杂协作的收益需要更大任务集进一步测量。

### Stage 8：任务状态与真实编码迭代

默认主 agent 动态委派，计划和验收是工具，不引入新的固定 workflow。进度状态、检查命令证据和哈希落盘；checkpoint 后换简短工作上下文，原会话保留；用户纠正待审核，文件工具有备份/diff/冲突回滚。

真实 Expense Tracker 项目经历了人为中断、输出截断、预算耗尽、测试文件布局不匹配和反复读取。改进不能只加预算：补具体 schema 错误、保留诊断尾部、保存不完整报告、可调整未完成目标路径及无进展提醒。最终三个目标均 passed，38 个 unittest 与独立黑盒审查通过。年 0 的业务缺陷由独立审查发现后交给 agent 修复；最终口头摘要也曾说错退出码，因此不能只看 agent 的自我汇报。

详细成本与失败轨迹见 experiments/long-task-validation.md。当前上限适合更长任务，但单次试验仍消耗较多 token，Stage 9 应优先测量并减少无效工作。
