# 当前学习进度

更新日期：2026-10-18。仅保留当前实现对应的总结，详细计划见 [TODO](TODO.md)。

| 阶段 | 当前成果 | 学习重点 |
| --- | --- | --- |
| Stage 0 | uv、配置模板、本地 key 配置、依赖锁 | 环境与依赖可复现 |
| Stage 1 | `examples/minimal_agent.py` | 模型 → 工具 → 结果回填 → 最终答案 |
| Stage 2 | DeepSeek/GPT 适配、文件/SQLite 工具 | 协议续接、参数校验、失败与重复调用 |
| Stage 3 | 可选 Local/Semantic/DDG 检索、Qwen embedding/rerank | 分层召回、增量 HNSW、证据与排序 |

RAG 默认关闭；开启后由 agent 决定是否调用。设备 `auto` 优先 CUDA/MPS，最后 CPU；CUDA 使用 FP16。当前执行环境只能实测 CPU，3080 Ti 的性能仍需在对应机器验证。

当前测试共 82 项，覆盖保留的 Stage 1/2 与分层 RAG。删除了旧 RAG 实现及其 21 项测试、旧索引和过期评测数据；当前知识库与模型缓存保留。验证详情见 [RAG 验证](experiments/rag-validation.md)。

下一步是 Stage 4 的短期上下文、会话恢复和显式长期记忆。RAG 后续可继续研究大语料 recall/延迟、索引压缩与清理、可选引用校验。当前不把检索分数当作事实概率，也不声称引用位置就能证明语义支持。
