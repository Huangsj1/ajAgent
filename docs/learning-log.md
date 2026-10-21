# 当前学习进度

更新日期：2026-10-20。仅保留当前实现对应的总结，详细计划见 [TODO](TODO.md)。

| 阶段 | 当前成果 | 学习重点 |
| --- | --- | --- |
| Stage 0 | uv、配置模板、本地 key 配置、依赖锁 | 环境与依赖可复现 |
| Stage 1 | `examples/minimal_agent.py` | 模型 → 工具 → 结果回填 → 最终答案 |
| Stage 2 | DeepSeek/GPT 适配、文件/SQLite 工具 | 协议续接、参数校验、失败与重复调用 |
| Stage 4 | context builder、JSONL 会话、显式记忆 CLI、AJAGENT.md | 输入与历史分离、完整协议批次、可恢复摘要 |
| Stage 3 | 可选 Local/Semantic/Tavily 检索、Qwen embedding/rerank | 分层召回、增量 HNSW、证据与排序 |

RAG 默认关闭；开启后由 agent 决定是否调用。设备 `auto` 优先 CUDA/MPS，最后 CPU；CUDA 使用 FP16。当前执行环境只能实测 CPU，3080 Ti 的性能仍需在对应机器验证。

当前测试共 128 项，覆盖保留的 Stage 1/2 与分层 RAG。删除了旧 RAG 实现及其 21 项测试、旧索引和过期评测数据；当前原文已迁到 knowledge/，索引已在 knowledge/.index/ 重建；模型缓存保留。验证详情见 [RAG 验证](experiments/rag-validation.md)。

Stage 4 已完成短期上下文、会话恢复和显式长期记忆；下一步是 Stage 5 的 harness。RAG 后续可继续研究大语料 recall/延迟、索引压缩与清理、可选引用校验。当前不把检索分数当作事实概率，也不声称引用位置就能证明语义支持。

知识库可独立于项目 workspace：`update_knowledge(source, output)` 构建/更新，`open_knowledge(output)` 从索引读取源目录；agent 用 --rag-index 指定索引。Local Search 用 --rag-local-path 单独指定外部目录或文件。tools 分为 files/database/retrieval 三个目录。CLI 路径相对当前目录，TOML 路径相对配置文件所在目录。

Web Search 采用 Tavily Python SDK，认证与参数集中在 `[tavily]`。移除旧搜索依赖和重试/冷却逻辑，保留单次请求、结果归一化与直接错误返回。107 项测试通过，使用本地 key 的 SDK 真实搜索成功返回 3 条结果，无 warnings。

Stage 4 新增 13 项离线测试；DeepSeek 与 OpenAI 均通过真实两轮 JSONL 会话恢复验证。详见 [上下文与记忆验证](experiments/context-memory-validation.md)。

2026-10-21：新增累计/最后请求用量区分、摘要费用只累计一次、会话批次/大小缓冲刷新和中断刷新测试；全套 125 项通过。写盘优化未做性能基准，不报告具体加速比例。

Session total usage 改为跨 resume 累计，最近 end 是累计快照；新增新会话归零、无 end 恢复、连续恢复与同对象复用、错误结束及摘要增量验证，全套 128 项通过。
