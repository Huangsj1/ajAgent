# Stage 4 验证

验证日期：2026-10-20。

## 离线验证

运行 `uv run --extra local-models pytest -q`：**120 项通过**，其中新增 13 项 Stage 4 测试。既有模型、工具、RAG 测试继续通过。

- JSONL 会话创建、第二次加载后续接、新会话隔离；不同 provider/model 拒绝复用。
- 显式记忆新增、来源和时间记录、原 ID 修改、遗忘、数量/长度边界；Markdown 与 JSON 更新。
- 同一问题关闭/开启记忆时，用可观察的测试模型确认输入与输出差异，开启时 estimated 上下文增大；forget 后新加载的记忆为空。
- DeepSeek call/tool 与 OpenAI reasoning/function_call/function_call_output 在裁剪及恢复时保持完整；原始 10000 字符工具结果仍在 JSONL。
- 超限先裁剪副本；摘要保留最近批次、最新用户消息和证据位置；重复构建复用摘要。
- 摘要请求失败、摘要过大、最新输入过大时结束，不反复调用，原始历史及压缩状态不被覆盖。
- CLI 加载 AJAGENT.md，`--no-memory` 只关闭记忆，恢复摘要检查点。

CLI 帮助与 `git diff --check` 均通过。

## 真实 API 冒烟

读取现有 config.local.toml，在临时会话目录分别调用 DeepSeek 与 OpenAI，两家均完成：

1. 第一轮给定任务代号 `ORION-742`，模型返回 completed。
2. 重新从 JSONL 加载同一会话，询问上一轮代号；返回 completed，答案包含正确代号。

共四次模型请求。未打印或写入厂商凭据，测试会话目录退出后自动清理。

真实 API 冒烟覆盖会话恢复；记忆开关、遗忘、压缩失败和预算边界使用离线模型验证。未声称真实模型摘要无损，也未运行长会话压力或并发写入测试。输出预算通过两家 API 验证可接受，输入 token 估算仍是启发式值。
