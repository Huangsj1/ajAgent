# 当前 RAG 验证

更新日期：2026-10-20。对应当前 Local/Semantic/Tavily + Qwen reranker 实现。

## 自动测试

```bash
uv run --extra local-models pytest -q
```

清理后 **107 项通过**。旧 RAG 实现及对应 21 项测试已删除；现有功能测试保留。

| 范围 | 覆盖 |
| --- | --- |
| 基础 agent | 最小循环、双厂商协议、文件/SQLite 工具、错误处理与重复调用 |
| Local | 真实 rg、Python/TypeScript AST、非法正则、路径过滤、语法错误 |
| Semantic | 真实 HNSW/SQLite/NumPy；增删改、向量复用、过期过滤、失败保留快照、ANN 对照精确排名 |
| 文档 | 章节/代码分块、真实 PDF 提取、查询不再解析 PDF |
| Web | Tavily 请求契约、认证、去重、分数、空结果、HTTP/网络错误、密钥脱敏、不重试 |
| Service | RRF、rerank、部分失败、输出预算、可选工具注册与无检索回答 |
| 知识库接口 | 外部源/输出目录、输出在源内部、仅凭索引打开/更新、索引迁移、错误源目录拒绝；agent 路径参数传递 |
| Local 范围 | 独立目录或单文件搜索；不扫描单文件的兄弟文件；跨来源绝对路径去重 |
| Device | CUDA/MPS/CPU 选择、显式设备错误、FP16 参数传递；使用模拟设备 |

## 当前实现的真实验证

- Qwen embedding 和 reranker 已实际下载并在 CPU 上运行。知识库 12 篇、12 个 chunk；重复建索引无需新增 embedding。
- 使用 config.local.toml 的 Semantic + rerank 查询命中 Orion v2 的 6 小时依据。
- Tavily 实际查询返回有效结果，无 warnings。
- DeepSeek 调用 retrieve 与基础工具后回答 6 小时，并引用文档行号；GPT 在开启 RAG 时可以不调用工具直接回答。
- 当前执行环境未检测到 CUDA/MPS。3080 Ti 的实际速度、显存使用与精度尚未测量。

## 清理后的数据约束

`.ajagent` 保留 `models/`；原文和当前索引分别位于 `knowledge/`、`knowledge/.index/`。清理后已在离线模式下验证当前索引与两个模型缓存能完成检索和精排。索引保留 CURRENT 指向的 generation、锁文件及对应正文缓存；两个 Qwen 固定 revision 的模型快照及其关联文件保留。配置中的 key 和回答模型选择未更改。

这不是大规模质量 benchmark。尚未完成新版完整问答质量集、大语料 recall/延迟与独立人工事实核对；当前没有自动最终引用或语义蕴含验证。

## 独立知识库接口验证

默认原文迁移到根目录 knowledge/，通过真实 Qwen 模型构建 knowledge/.index/：12 篇新增、12 个新向量。随后仅传 `--index knowledge/.index` 更新：12 篇 unchanged、0 个新向量。读取 local TOML 并使用 Semantic + reranker 查询 Orion v2，命中 knowledge/05-release-v2.md 的 6 小时依据，返回绝对文件路径、无 warnings。

构建和查询成功后删除了原 .ajagent 下的索引；examples 下只保留最小 agent 示例。example/local TOML 同步路径，API key 和厂商模型选择保持不变。文档中的命令采用当前 index/update/search 与 agent --rag-index/--rag-local-path 参数。

## Tavily 接入验证

Web Search 已使用 Tavily SDK 替换旧搜索实现，删除旧依赖、专用适配和重试/节流/冷却代码。107 项测试通过；HTTP 400/401/422/429/432/433/500、网络超时、连接失败均验证只请求一次。还覆盖空结果、无效 JSON、缺 key 不请求、服务端错误脱敏及环境变量优先级。

读取实际 config.local.toml，通过官方 SDK 搜索 Tavily Python SDK search documentation，返回 3 条结果，status=ok、attempts=1、无 warnings；摘录长度分别为 1448、1421、1455 字符，均在默认 2000 字符内。测试还覆盖 advanced 参数、自定义截断、无重试及配置校验。未修改或打印厂商凭据。
