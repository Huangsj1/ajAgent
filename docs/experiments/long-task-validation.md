# Stage 8：真实长任务编码与恢复验证

日期：2026-10-23。模型：DeepSeek `deepseek-flash`。默认主 agent 动态调用 delegate，没有启用 Stage 7 固定 workflow。代码产物由 ajAgent 编写，外围助手提供需求/CSV、运行试验、审查和针对性反馈，未替它手工实现 expense.py 或其测试。

## 项目与独立验收

产物：`examples/projects/expense_tracker/`。Python 标准库 CLI，CSV 验证、整数分金额、SQLite 原子导入/重复跳过、月份报表、unittest 与 README。

- agent 生成的 **38 项 unittest 全部通过**，无重复汇总加载器。
- 外部 `evals/long_task_audit.py` 再从 CLI 黑盒验证：重复导入仍为 3 行/3550 分；2026-05 为 2 行/3050 分；引号、逗号和 Unicode 保留；坏行出现在有效行之后也不部分提交；NaN/Infinity、非正金额、超精度、非法日期/月/年份均拒绝且无 traceback。
- 持久化计划 `8d2f70efdef04fd7bc1fc5dba6d9b9e0` 的 core/tests/verify 最终均 passed，存有真实检查结果和文件哈希。
- 实际成功使用 15 种工具：plan_tasks、start_task、task_status、update_task_paths、checkpoint_task、verify_task、delegate、list_files、read_file、search_files、write_file、read_artifact、run_command、run_python、query_sqlite。没有为了凑数量强行开启网络、浏览器或 MCP。

```sh
cd examples/projects/expense_tracker
uv run python -m unittest discover -s tests -v
uv run python expense.py import --db expenses.sqlite --csv sample.csv
uv run python expense.py report --db expenses.sqlite --month 2026-05
```

在项目根目录运行外部审计：

```sh
uv run python evals/long_task_audit.py
```

生成的 SQLite 和 agent 日志被 Git 忽略，源代码/测试/需求/CSV/README 保留。恢复状态与原始调用记录在该示例目录自己的 `.ajagent/`，不混入主项目任务。

## 真实运行全过程，包括失败

| 运行 | 终止状态 | 步数 | input / output token | 耗时 |
| --- | --- | --- | --- | --- |
| 初次编写 | 人为 SIGINT，中断恢复试验 | 20 | 209189 / 13155 | 76.8s |
| 新会话续接 | 单请求输出截断 incomplete | 9 | 193474 / 7293 | 48.9s |
| 提高输出额度后续接 | 700000 总 token 预算耗尽 | 43 | 667086 / 19389 | 135.5s |
| 引入上下文交接后续接 | 80 步上限，陷入测试加载器排查 | 80 | 1135848 / 17111 | 229.9s |
| 修复任务路径更新后交付 | completed | 56 | 736660 / 13375 | 139.4s |

费用统计来自各主 run，包含委派及摘要；中断中的未知服务器计费不能由客户端完整确认。不同运行完成不同部分，不能把它们当作严格同任务 A/B 性能实验。此次并非“一次就成功”，消耗明显偏高；增加预算解决硬上限，不能代替减少重复读取与任务跑偏。

第一次在 core 验收完成、tests 刚开始且无 pending 副作用时发送 SIGINT。新会话通过 --task 恢复：core 原状态仍 passed，tests running，verify pending，没有重做未变动的 core。后来实际修复 expense.py，哈希变化正确使 core 回到 pending，必须重新验收。

## 发现与改进

1. **输出截断丢失已有分析**：原先子报告达到 4096 token 只返回“未完成”。现在保留显式标记的 partial 文字及完整报告 artifact；截断的工具调用仍不执行。单请求输出配置升为 8192，委派提示约束报告长度。
2. **参数错误无法定位**：read_artifact length=4000/3000 超过原 2000，模型连续猜测。错误现在包含字段与 schema 约束，不回显输入值；artifact 读取配置提高到 8000。
3. **诊断尾部被丢弃**：unittest 失败常在日志末尾。命令与大结果预览保留 head/tail，避免模型仅见前面一堆 ok 而反复分页。
4. **历史反复发送**：checkpoint 或验收通过后，用持久化任务事实换新工作上下文，保留原始 session、当前请求和累计 usage。最后运行有 4 次明确 task_context_checkpoint。连续观察而无文件工具/状态更新时提醒推进下一验收，不自动清空失败或续费重跑。
5. **初始文件布局绑死**：计划限定 tests/test_expense.py，但实现已拆成多模块，模型绕去编写复杂汇总加载器、重复收集测试。新增 update_task_paths，只更新未完成目标路径并记录理由，保持验收条件/命令不变。删除额外加载器后恢复 38 个独立测试。
6. **真实代码缺陷**：独立审计发现 --month 0000-01 被接受。交由 agent 修复年范围并补 4 项边界测试；黑盒重测通过。过程中还修复了 header-only CSV 和测试期望/报表排序问题，未将空测试当完成。
7. **最终口头总结仍可能错**：一次最终文字把输入错误退出码说成 1；实际代码和 README 均为 2。未把该口头说法当作验收事实，并在系统说明中强化“只总结已核验数字”。结构化通过不保证自然语言总结无误，代码/命令结果仍是依据。

基于实测，主运行模板现在为 100 steps / 300 tool calls / 200 万 token / 30 分钟，单次输出 8192；仍为有限上限。试验最终运行的专用配置为 80 steps / 240 calls / 150 万 token / 1200 秒，不是靠无限循环直到成功。主配置只自动批准内部任务跟踪；试验配置才显式批准本示例 workspace 的写入/执行。

## 离线边界测试

最终完整回归：**266 passed**（含 38 项生成项目测试、Chromium 与 MCP）；Stage 8 专项 13 项通过。

`tests/test_long_running.py` 启动真实子进程，三段任务在第二段 os._exit 后恢复；覆盖旧 passed 项不重复、验证失败不能被声明通过、外部文件变更失效、任务锁与未知副作用保护、文件回滚冲突、用户反馈显式审核、上下文交接保留历史与 usage、布局更新保留原检查、命令诊断尾部和截断工具调用不执行。

本阶段没有后台服务、自动无限续跑或任意命令回滚。脚本直接修改文件不在文件工具备份范围内，测试仍需有意义。研究方向仍是减少无效工作，而不是继续提高预算掩盖问题。
