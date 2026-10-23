# Stage 7：单 agent 与多 agent 对比

日期：2026-10-23。真实 API：DeepSeek `deepseek-flash`；10 个任务 × 2 种模式 × 3 次，共 60 次运行。不是 scripted model 分数。

## 实验方法

`uv run python evals/compare.py --workers 3`。两种模式均只开放 files，禁用记忆与委派；使用相同本地资料和目标、每任务 20,000 token / 20 次工具调用 / 180 秒总预算、1,200 output token 请求上限。多 agent 内部节点顺序运行，不为每个节点重置预算。3 个独立实验任务可并发；耗时含网络等待，不是严格隔离的性能基准。

任务覆盖学习时间、重试规则、版本冲突、分块、向量、记忆、SQLite、引用、恶意文档指令和工具执行协议。提问按指定知识文档回答，不把历史教学资料当作当前实现规格。

指标：completed 是运行状态；引用可追溯要求至少一个 [e:ID] 且都来自实际观察；预期事实检查是公开 expected_terms 的关键词匹配。综合通过要求三个条件同时满足。这些自动指标不等于完整语义支持度或人工质量评分。

| 模式 | 完成 | 引用可追溯 | 预期事实 | 综合通过 | 平均耗时 | 总 token |
| --- | --- | --- | --- | --- | --- | --- |
| single | 30/30 | 28/30 | 30/30 | 28/30 | 3.03s | 78,529 |
| multi | 26/30 | 30/30 | 30/30 | 26/30 | 8.60s | 341,504 |

多 agent 的可追溯计数包含预算耗尽前留下的草稿，不能据此把 partial/budget_limit 计为任务完成。

## 所有失败样本

| 任务 | 模式/重复 | 原因 |
| --- | --- | --- |
| citation | single/1 | 缺少合法引用格式或引用 ID 不在观察证据中 |
| memory | multi/1 | budget_limit；此前重试：reviewer: Each issue needs location, problem and suggestion |
| citation | multi/1 | budget_limit；此前重试：writer: Invalid control character at: line 1 column 175 (char 174) |
| study | single/2 | 缺少合法引用格式或引用 ID 不在观察证据中 |
| memory | multi/2 | budget_limit |
| version | multi/3 | budget_limit |

## 解释与限制

这组短资料问答中，多 agent 没有更好：综合通过率 86.7%，低于单 agent 的 93.3%，总 token 约 4.35 倍，平均耗时约 2.84 倍。角色交接和重复审阅占用预算；更严格的输出契约也引入失败。不能把此结论推广到复杂研究任务，当前案例规模和关键词指标都有限。

语义支持抽查：版本题引用文档明确区分 v1=24 小时、v2=6 小时；恶意文档题回答 Ada/蓝色；引用题明确指出来源 ID 存在不能证明结论。检查到的基本事实符合指定资料，但这不是对 60 份答案逐句进行的独立人工标注。保留原始答案、run_directory、usage 和失败列表供复核，不宣称全量语义正确率。

完整离线回归 215 项通过（含 Chromium 和 MCP）。单独的双厂商 workflow smoke 均 completed；离线测试确定性覆盖 review 失败→reviser→review 通过、缺证据 partial、父预算终止和并行 delegate 的层级限制。初次真实 smoke 因主会话问候记忆污染 JSON 合同而失败，修复为角色仅继承项目指令，不继承对话偏好；修复后重新通过。

原始记录：`.ajagent/eval-results/multi-agent.jsonl`（本地保留，不提交 API 日志）；任务集：`evals/cases/multi_agent.jsonl`；脚本：`evals/compare.py`。
