# Stage 5 验证

验证日期：2026-10-21。

## 离线验证

`uv run --extra local-models pytest -q`：146 项通过，包括既有 Stage 1–4 回归测试。

新增测试覆盖：

- 假模型串联真实工具：读取 → 写入失效 Markdown 引用 → 验证失败 → 修正引用 → 完成；按路径的 session 授权只询问一次。
- 写演示 Python 模块 → 指定检查失败 → 修改代码 → 检查通过。验证命令也走权限和预算。
- allow/ask/deny、非交互时 waiting_for_user、before_tool 阻断、before_finish 两轮修订上限。
- step/tool/token/wall-clock 限额、响应后超 token 停止、用户补充进入下一步上下文。
- 大工具结果保存 artifact，模型收到读取位置；配置密钥不出现在 session、事件、checkpoint 或 artifact 中。
- 命令输出限长、无 shell、超时终止；Ctrl-C 异常路径回收实际启动的子进程。
- 完成批次恢复时不执行原有工具；执行后在 after_tool 模拟中断，恢复进入 unknown_effect，核对确认后才继续。
- session 授权在恢复后继续有效，新 session 不继承；session total usage 继续累计，摘要事件与费用只计一次。
- `evals/cases/harness.jsonl` 的离线用例；trace replay 不调用模型或工具。

## 真实 API 冒烟

使用现有本地配置，在独立临时 workspace 放入 source.md，内容包含 ORION-742。分别让 DeepSeek 和 OpenAI：读取 source.md，创建带任务代号及本地来源链接的 answer.md，然后完成。

| 厂商 | status / state | 工具调用数 | 正确代号与来源 | 遗留 pending |
| --- | --- | --- | --- | --- |
| DeepSeek | completed / completed | 2 | 是 | 0 |
| OpenAI | completed / completed | 2 | 是 | 0 |

这次程序化冒烟通过测试回调允许临时文件写入；正式 CLI 仍逐次或按 session 请求用户授权。未改写用户知识库或打印密钥；临时目录在验证后清理。

## 验证边界

已在当前 macOS/Python 环境验证 POSIX 子进程清理，未测试 Windows。没有做断电/文件系统故障实验，不宣称 fsync 持久化或 exactly-once。真实 API 冒烟验证基本读写与引用检查；修订失败、预算、权限拒绝、取消和未知副作用使用离线可控测试。

Token 输入预算是估算；模型报告的实际消耗可能超出预留，超出后记录并停止。进程内计算只能在调用边界检查时间。Markdown 检查仅覆盖行内本地文件目标，不证明引用语义正确。无性能加速或价格结论。
