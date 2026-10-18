# 当前 RAG 验证

更新日期：2026-10-18。对应当前 Local/Semantic/DDG + Qwen reranker 实现。

## 自动测试

```bash
uv run --extra local-models pytest -q
```

清理后 **82 项通过**。旧 RAG 实现及对应 21 项测试已删除；现有功能测试保留。

| 范围 | 覆盖 |
| --- | --- |
| 基础 agent | 最小循环、双厂商协议、文件/SQLite 工具、错误处理与重复调用 |
| Local | 真实 rg、Python/TypeScript AST、非法正则、路径过滤、语法错误 |
| Semantic | 真实 HNSW/SQLite/NumPy；增删改、向量复用、过期过滤、失败保留快照、ANN 对照精确排名 |
| 文档 | 章节/代码分块、真实 PDF 提取、查询不再解析 PDF |
| Web | 固定 DuckDuckGo、URL 去重、缓存、错误与空结果 |
| Service | RRF、rerank、部分失败、输出预算、可选工具注册与无检索回答 |
| Device | CUDA/MPS/CPU 选择、显式设备错误、FP16 参数传递；使用模拟设备 |

## 当前实现的真实验证

- Qwen embedding 和 reranker 已实际下载并在 CPU 上运行。知识库 12 篇、12 个 chunk；重复建索引无需新增 embedding。
- 使用 config.local.toml 的 Semantic + rerank 查询命中 Orion v2 的 6 小时依据。
- DDG 实际查询返回有效结果，无 warnings。
- DeepSeek 调用 retrieve 与基础工具后回答 6 小时，并引用文档行号；GPT 在开启 RAG 时可以不调用工具直接回答。
- 当前执行环境未检测到 CUDA/MPS。3080 Ti 的实际速度、显存使用与精度尚未测量。

## 清理后的数据约束

`.ajagent` 只保留 `models/` 和 `rag-index/`。清理后已在离线模式下验证当前索引与两个模型缓存能完成检索和精排。索引保留 CURRENT 指向的 generation、锁文件及对应正文缓存；两个 Qwen 固定 revision 的模型快照及其关联文件保留。配置中的 key 和回答模型选择未更改。

这不是大规模质量 benchmark。尚未完成新版完整问答质量集、大语料 recall/延迟与独立人工事实核对；当前没有自动最终引用或语义蕴含验证。
