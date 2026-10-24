# Snake 任务预算诊断（2026-10-24）

## 原始日志证据

目前根 `.ajagent/` 只有一组完整 session/run，可以确认这一次的原因，无法推断已删除的其他尝试：

- run：`1c50b0e19b9a48a98db64598035db905`
- session：`fa1b04f7b3d349ddabf4e02abdcbfa00`
- 请求：在 examples 下创建简单终端贪吃蛇。
- 实际 limits：100 steps、300 tools、3,000,000 tokens、1,800 秒。CLI 已把 local 的 100 steps 传入循环，并非当时 RuntimeDefaults 中的 8 步回退值。
- 第 90 步进入 FINALIZING 时，还剩 10 steps、198 tools、633,793 tokens、1,500 秒。结束耗时 304.5 秒，实际 input/output 为 2,329,210 / 41,092。
- 直接原因：旧算法将所有维度统一取剩余比例，steps 只剩 10% 就收尾；仅在 `step == max_steps` 时返回 step_limit，导致本次错误地显示通用 budget_limit。

一 step 是一次主循环模型轮次，不是固定时间。模型几秒完成一轮时，数分钟就可能运行几十到上百轮。wall-clock 上限为半小时，不代表其他预算也能支撑半小时。

## 为什么不能只提高 steps

102 次工具调用中，有 29 次 write_file、28 次 read_file、25 次 run_command、14 次 run_python。开头六步读取无关 expense_tracker 文件、查版本；简单游戏扩展到 600 多行主模块、58 项测试以及 hazards / demo / scripted drive 等功能。

后半程反复写入并执行 patch_tmp.py，在最小棋盘高度 4 与 5 之间切换。自写测试一方面要求 `Game(5, 4)` 报错，另一方面用 `scripted(6, 4, ...)` 测绘制；CLI 测试又要求 `--height 4` 报错。模型没有先确立统一约定，修改实现后又被另一组测试拉回。原项目独立复测仍为 56 项通过、2 项失败。

此外，rg 命令多次返回通用 io_error，未能区分可执行文件不在 PATH 与搜索路径错误。这类不明确错误会诱发更多猜测。当前进程可找到 rg，不能据此断言用户当时的 PATH 完全相同；本次增加明确的 executable_not_found 诊断。

## 改动

1. steps 的收尾预留改成 `final_step_reserve=1`，100 步预算可以工作到第 99 步，第 100 步收尾。LOW 仍参考步骤比例；时间、tokens、工具仍使用资源比例保护。
2. 新增 `phase_reason`，区分 steps / tokens / tools / time / request_headroom / repeated_batches / parent；steps 收尾正确返回 step_limit。
3. CLI 启动打印有效上限，日志 run_started 保存实际配置，结束显示阶段触发资源。
4. 默认与 example/local 同步为 **300 steps / 900 tools / 6,000,000 tokens / 3,600 秒**。这是容量上限，不承诺一定跑满一小时；扩大 tokens 也会增加潜在 API 用量。
5. 主循环提示要求最小交付范围，并在测试失败时同时检查实现与断言，识别自写测试的矛盾，避免来回打补丁；不得为通过而削弱外部提供的测试。
6. 缺失命令返回明确 executable_not_found，建议改用现有工具或安装依赖。

## 同项目恢复验证

将原项目复制到 `.ajagent/snake-validation/workspace/examples/projects/snake`，原项目、原 session 和 run 保留不变。测试使用新的配置和新会话，要求 ajAgent 完成现有项目、文档及验证；未向它提供具体补丁。该试验只启用文件、执行、artifact 工具，自动审批仅对隔离试验配置生效。

新 run `578ab8fd7f5c4b1a8767525bf87c9882`：

| 指标 | 结果 |
|---|---|
| 状态 | completed |
| 主循环 steps | 57 |
| 工具调用 | 76 |
| 用时 | 120.8 秒 |
| Input / output tokens | 1,353,468 / 8,648 |
| 原有测试 | 58 项通过 |

Agent 统一最小高度为 5，修正自写绘制 fixture 为 6×5（保留绘制断言），补 README，并清理旧临时脚本。仍有多余读取和较高输入 token 消耗，因此本次成功不代表效率已理想。它从已有代码接手，不能和原先从零生成直接当作性能 A/B 对比，也不能证明单纯增加预算导致成功；它事实上没有使用到新增的第 100 步之后额度。

独立检查另外运行了项目测试、demo、脚本移动、错误参数退出码，并使用真实伪终端检查 curses 绘制和 q 退出。检查脚本与结果保留在 `.ajagent/snake-validation/audit.py` / `audit.json`；PTY 检查最初因父进程等待时未持续读取终端输出而超时，修正测试驱动后通过，未为此修改游戏。

Harness 回归：`uv run pytest -q tests` → **247 passed, 2 skipped**。这里显式测试 agent 自身；原始未完成 snake 的两项失败单独记录，不算作 harness 回归通过。新增测试覆盖 90% steps 不再停止、触发资源标记、缺失命令错误。

## 第 119 步 API 错误（后续运行）

运行 `c66828f96c31424f9d4bbc0d667bb773` / session `ecd469838a224373b40d68f3ac691622` 在第 119 步报 `api_error`：请求耗时 0.835 秒，停止前累计 input/output 为 3,351,192 / 35,370。该次旧日志只记录 ModelError 类名，没有 HTTP 状态码，无法从历史记录唯一确定错误原因。

用保存的 summary + 未压缩消息和当前工具定义重建一次模型请求（没有执行返回的工具），得到 **HTTP 402**。这是上下文重放而非逐字节 HTTP 报文重放；原始动态 budget 提示未持久化。根据 [DeepSeek 官方错误码](https://api-docs.deepseek.com/quick_start/error_codes/)，402 表示余额不足。因此当前可确认的阻断来自供应商计费，不能靠增大 steps 或自动重试解决；历史第 119 步是否完全相同，受原日志缺失限制，不能作绝对断言。

现在 ModelError 带有安全的结构化 kind / message / http_status / attempts / request_id，写入 runs 的 model_finished.error_details 和 sessions 的 model_error。不记录完整 API 响应、Authorization 或密钥。402 显示明确的余额/计费提示且不重试；429、5xx 和网络失败仍遵循原有限次重试。

预算实现统一命名为 `Budget`，去掉多余的类名和兼容别名；阶段控制、共享预留、计费功能不变。
