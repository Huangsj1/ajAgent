# Agent 执行流程（对照当前实现）

本文以默认 `single` 模式的 `Runner.run()` 为主线。模型决定下一步做什么；`run_agent()` 维护模型与工具交互循环；`Runner` 提供预算、权限、调度、验证、恢复与持久化能力。可选的固定 research workflow 另见第 7 节。

## 1. 对原流程总结的补充与修正

| 环节 | 当前代码的准确行为 |
| --- | --- |
| 恢复来源 | `Session` 先从 JSONL 恢复完整消息组、授权、累计用量、上一 run ID 和最近的 context 摘要；CLI 恢复 `builder.summary/covered`；`Runner.run()` 再加载关联 run 的 `checkpoint.json`，恢复 changed/evidence 并检查 pending。不是扫描所有 run 寻找最新目录。 |
| 新 run 与预算 | `Runner.__init__()` 创建 `Events` 时就生成新的 run ID；恢复检查通过后才把它绑定到 session。正常 CLI 恢复会创建新 Runner/Budget，运行预算重新开始，session 累计用量保留。 |
| 未知副作用 | pending 表示上次没有完成整个工具消息批次的持久化，副作用可能已发生。缺少 `--acknowledge-unknown` 时返回 `unknown_effect`；确认后继续，不自动重放旧调用，也不据此证明上次成功。 |
| 预算阶段 | 正确名称是 `HIGH_BUDGET → LOW_BUDGET → FINALIZING`，只向前推进。`advance(step)` 综合 token、工具次数、时间和步数；模型请求前再加入本次请求大小判断，不是前者只看 step、后者只看 token。 |
| 系统提示词 | 每步创建 `active_system = system_prompt + Budget.prompt()`，不反复修改基础 system_prompt；因此预算提示不会逐步重复累加。 |
| 两种上下文处理 | `ContextBuilder` 输入超限时才可能调用模型摘要；`TaskProgress.checkpoint()` 保存模型通过工具参数传入的 summary/next_step，再设置延迟交接，不额外调用模型。`verify_task` 通过后也可触发交接。 |
| 模型结果 | 必须先检查 `stop_reason == completed`。incomplete/refused/empty_output 等直接结束，不执行该响应里的工具，也不进入普通完成验证。 |
| 权限 | read 通常直接允许，但 deny 优先；write/execute 仅在没有自动批准或已保存授权时询问。未配置交互回调时返回 permission_required 工具错误，正常答案路径最终会被同名状态拦截。 |
| 并行条件 | 仅连续的 `effect == read AND parallel == True` 工具组成并行组。其他调用都是串行屏障；结果始终按原调用顺序回填。delegate 默认符合并行条件，task_status 则显式禁止并行。 |
| changed 与 evidence | 内置 changed 追踪成功的 write_file/apply_proposal/restore_file；不会自动发现任意命令修改的所有文件。`EvidenceMap.add()` 只接收成功工具的 sources，规范化并去重，来源记录本身不证明结论正确。 |
| 完成验证 | 除 Markdown 本地链接外，还执行 before_finish hooks（包括任务计划完成检查），有 changed 且配置了 validation_command 时执行验收命令。失败最多反馈修复 max_repairs 次，之后 validation_failed。 |
| 重复检测 | signature 包含工具名、规范化参数与序列化结果，并排序、排除 call ID。默认最近 12 个批次中同一 signature 第 2 次触发低预算提示，第 3 次立即无工具收尾，状态 no_progress；不要求连续重复。 |
| 最终收尾 | finalize 使用原始请求、预算提示和能容纳的最近完整工具证据，不纳入 assistant 计划文字，不携带工具；绕过普通 before_finish 验证，保留 step_limit/budget_limit/no_progress 原因。硬预算或时间耗尽可能无法再请求收尾。 |

默认值来自 [options.py](../src/ajagent/options.py) 的 ContextOptions 与 HarnessOptions，可被配置覆盖。主流程依据 [loop.py](../src/ajagent/core/loop.py) 第 22、44 行和 [runner.py](../src/ajagent/harness/runner.py) 第 282 行。

## 2. 启动、恢复与主循环总览

```mermaid
flowchart TD
    A([CLI: prompt / resume / task]) --> B[加载配置、项目指令与长期记忆<br/>创建 Session / ContextBuilder / Runner]
    B --> C[ToolManager 装配工具与 hooks<br/>恢复会话摘要；绑定可选任务计划]
    C --> D[Runner.run<br/>配置预算、记录 run_started]
    D --> E{有关联的旧 run checkpoint?}
    E -->|有| F[恢复 Hooks.changed 与 EvidenceMap]
    F --> G{pending 非空且未确认?}
    G -->|是| U([unknown_effect<br/>检查工作区后用 acknowledge-unknown 继续])
    G -->|否| H[绑定新 run ID，保存初始 checkpoint]
    E -->|无| H
    H --> I[run_agent: 读取会话消息组<br/>追加本次用户请求]
    I --> J[每一步 before_step<br/>检查预算、推进阶段、处理任务交接与补充输入]
    J --> K{FINALIZING?}
    K -->|是| Z[finalize: 一次受预算限制的无工具收尾<br/>保留停止原因，跳过普通完成验证]
    K -->|否| L[追加 Budget.prompt<br/>ContextBuilder.build 构建本次输入]
    L --> M[ObservedModel.generate<br/>估算、预留、请求模型、结算真实 usage]
    M --> N{请求前要求收尾?}
    N -->|FinalizeRequired| Z
    N -->|收到响应| O{stop_reason 为 completed?}
    O -->|否| P[返回 incomplete / refused / empty_output 等状态]
    O -->|是| Q{包含 tool_calls?}
    Q -->|是| R[execute_many<br/>按权限与并行规则执行工具]
    R --> S[序列化结果，按 call ID 回填<br/>record 完整消息批次并 flush]
    S --> T[batch_finished<br/>清空 pending，保存 checkpoint]
    T --> V{重复批次次数达到阈值?}
    V -->|是| W[reason = no_progress]
    W --> Z
    V -->|否| X[必要时降至 LOW_BUDGET<br/>追加改变策略的提示]
    X --> J
    Q -->|否| Y[record 答案并 flush<br/>batch_finished 后执行 before_finish]
    Y --> AA{验证决定}
    AA -->|允许修复| AB[追加验证错误反馈<br/>repairs 加一]
    AB --> J
    AA -->|通过| AC[返回 completed；空文本为 empty_output]
    AA -->|权限未满足或修复次数耗尽| AD[返回 permission_required / validation_failed]
    Z --> AE[run_agent 汇总用量<br/>Session.finish / flush]
    P --> AE
    AC --> AE
    AD --> AE
    AE --> AF[Runner 更新 RunState<br/>保存最终 checkpoint，记录 run_finished]
    AF --> AG([返回 RunResult])
```

图中 `J → … → J` 受 `for step in range(1, max_steps + 1)` 限制。默认在最后一步进入 FINALIZING；若循环自然耗尽，直接返回 step_limit。预算检查还可能抛出硬限制异常，处理见第 5 节。

启动依据 [cli.py](../src/ajagent/cli.py) 第 76 行、[session.py](../src/ajagent/memory/session.py) 第 10 行、[ToolManager](../src/ajagent/tools/runtime/loader.py) 第 38 行；主循环见 [loop.py](../src/ajagent/core/loop.py) 第 77 行。

## 3. 上下文构建与每次模型调用

```mermaid
flowchart TD
    A[ContextBuilder.build] --> B[deepcopy 尚未覆盖的完整 groups<br/>拼入 system、历史 summary 与当前用户请求]
    B --> C[按 UTF-8 字节粗估输入与 tool definitions<br/>输入上限 = max_tokens - output_tokens]
    C --> D{输入能容纳?}
    D -->|是| K[输出临时 context<br/>记录 stats；context_ready 结束构建阶段]
    D -->|否| E[只在副本上缩短 tool / function_call_output]
    E --> F{现在能容纳?}
    F -->|是| K
    F -->|否| G{允许摘要、存在较旧消息<br/>且摘要请求本身能容纳?}
    G -->|否| CE([context_error])
    G -->|是| H[旧 summary 加较早 groups<br/>通过同一个 ObservedModel 请求摘要，无工具]
    H --> I{摘要完整且压缩后能容纳?}
    I -->|否| CE
    I -->|是| J[更新 summary 与 covered<br/>保留 recent_groups 个完整组及当前用户请求]
    J --> K
    K --> L[ObservedModel.generate 主调用]

    L --> M[估算 context、工具定义与输出预留]
    M --> N[非收尾调用执行 Budget.advance<br/>同时考虑资源余额与 request_headroom]
    N --> O{进入 FINALIZING?}
    O -->|是| FR([抛 FinalizeRequired<br/>返回主循环转入收尾])
    O -->|否| P[阶段变化时刷新预算提示<br/>Budget.request 检查并预留 token]
    P --> Q[底层模型适配器发送 HTTP<br/>解析为统一 ModelTurn]
    Q --> R{请求成功?}
    R -->|是| S[同一预算锁内结算<br/>charge 实际 usage，再释放预留]
    S --> T[更新当前 Runner 及祖先 used<br/>主调用更新 last_usage；检查硬预算]
    T --> U([返回 ModelTurn])
    R -->|否| V[finally 释放未结算预留<br/>记录错误并向外抛出]
```

- H 的摘要调用也经过 M～V，同样占用运行预算；此时标记 purpose=summary，真实用量计入 used，但不覆盖主调用的 last_usage。
- 输入超限时最多尝试一次摘要；摘要关闭、最近消息本身过大、摘要输入过大或结果无效都会终止。原始 session 消息不被截断或覆盖。
- HTTP 层只重试请求本身：网络错误、429、5xx 按策略退避重试；不会重跑整个 step，也不会重放工具副作用。错误最终返回 api_error。
- `Budget.request()` 的 settle 在锁内先计入实际用量、再释放估算预留；异常退出只释放预留，没有可用 usage 时不会凭空记账。
- 图中的正常调用先执行 advance；finalize 已设置 finalizing 标记，会跳过再次要求收尾的判断，但仍受 request/check 约束。

实现依据：[builder.py](../src/ajagent/context/builder.py) 第 24 行、[compaction.py](../src/ajagent/context/compaction.py) 第 10 行、[harness/model.py](../src/ajagent/harness/model.py) 第 16 行、[budget.py](../src/ajagent/harness/budget.py) 第 135 行、[models/base.py](../src/ajagent/models/base.py) 第 29 行。

## 4. 工具调度、权限、副作用与结果

调度规则：连续的可并行 read 一组一组执行，遇到其他工具就等待前一组并串行执行。prepare 和 finish 按原调用顺序执行，只有 invoke 进入线程池。delegate 还受 subagent_slots 限制。

```mermaid
flowchart TD
    A[execute_many 按调用顺序遍历] --> B{下一调用为 read<br/>且 parallel 为 true?}
    B -->|是| C[收集连续的并行 read 组<br/>按序 prepare，invoke 提交线程池<br/>按序等待结果并 finish]
    B -->|否| D[串行屏障<br/>execute 完整处理这一调用]
    C --> E{还有调用?}
    D --> E
    E -->|是| B
    E -->|否| F[全部结果按原顺序交给主循环]

    P0[每个调用: _prepare_call] --> P1[记录 tool_started<br/>Budget.tool 检查并计工具次数]
    P1 --> P2[prepare: 工具查找、JSON 解析<br/>JSON Schema 参数验证]
    P2 --> P3[Hooks.before<br/>构建写入预览或 proposal 审批描述]
    P3 --> P4{Policy.decide}
    P4 -->|deny 或 ask 无回调| ER[生成失败 ToolResult<br/>ask 时标记 needs_permission]
    P4 -->|已有授权或用户允许| P5{effect 非 read?}
    P5 -->|是| P6[pending 追加 unknown 意图<br/>保存 checkpoint 后才执行副作用<br/>限制执行超时]
    P5 -->|否| P7[invoke handler]
    P6 --> P7
    P7 --> P8[成功或异常统一封装 ToolResult<br/>成功 sources 规范化为 source_record]
    P8 --> P9[_finish_call: Hooks.after<br/>追踪受支持文件工具的成功修改]
    ER --> P9
    P1 -.预算不足.-> ER
    P2 -.未知工具或参数错误.-> ER
    P3 -.hook 拒绝.-> ER
    P9 --> P10[脱敏结果<br/>仅成功时将 sources 加入 EvidenceMap]
    P10 --> P11{结果超过 artifact 阈值?}
    P11 -->|是| P12[将脱敏完整结果保存 artifacts<br/>data 改为 preview、artifact 路径和提示]
    P11 -->|否| P13[记录 tool_finished，返回结果]
    P12 --> P13
    P13 --> P14[主循环记录 tool_result 事件<br/>serialize 再限制模型可见输出<br/>通过 call ID 回填协议消息]
    P14 --> P15[record 完整 assistant 与工具消息组<br/>Session.flush]
    P15 --> P16[batch_finished<br/>清空整个批次 pending，保存 checkpoint]
```

权限按以下顺序生效：deny 优先；否则 read、auto_approve_tools、会话 allow_all、已保存 scoped grant 可直接允许；其余需要用户 once/session/allow_all 授权。write 的 scope 是工具名与 path，execute 使用完整参数。等待审批时先记录 waiting_for_user 并保存 checkpoint；授权决定返回后恢复 running。

pending 不在单个调用结束后删除。即使前面的写入已成功，后续调用中断导致完整消息批次未落盘，恢复时仍按 unknown_effect 处理。它保护的是“副作用与可重放消息批次之间的边界”，不代表文件工具有事务回滚。

输出限制有三层：Runner 将长结果保存为 artifact 并返回摘录；serialize 限制发给模型的 JSON 大小；ContextBuilder 在后续输入超限时再缩短历史观察。第三层的完整原文在 session 历史中，不意味着每次上下文截断都会新建 artifact。

EvidenceMap 的记录包含 id、uri、kind、title、locator，并保留原有 path/line 等字段；id 根据 kind、uri、locator 生成，用于去重。命令工具的 `ok=True` 只表示调用成功，还需看 exit_code 判断进程是否成功。

实现依据：[runner.py](../src/ajagent/harness/runner.py) 第 154、206、231 行、[policy.py](../src/ajagent/harness/policy.py) 第 18 行、[registry.py](../src/ajagent/tools/common/registry.py) 第 15、37、60 行、[evidence.py](../src/ajagent/tools/common/evidence.py) 第 6 行。

## 5. 完成验证、预算收尾与异常退出

```mermaid
flowchart TD
    A[正常 completed 模型响应<br/>没有 tool_calls] --> B[持久化答案批次<br/>batch_finished]
    B --> C[before_finish 调用 Hooks.validate]
    C --> D[before_finish hooks<br/>可含 TaskProgress 计划完成检查]
    D --> E[遍历 changed 中的 Markdown<br/>只检查有 path 且无 scheme 的链接是否存在]
    E --> F{有 changed 且配置 validation_command?}
    F -->|是| G[经 Runner.execute 执行验收命令<br/>记录结果、flush、batch_finished]
    F -->|否| H{needs_permission?}
    G --> H
    H -->|是| P([permission_required])
    H -->|否| I{有验证错误?}
    I -->|否| OK([completed；空文本为 empty_output])
    I -->|是| J{repairs 已达 max_repairs?}
    J -->|是| VF([validation_failed])
    J -->|否| K[repairs 加一<br/>追加验证错误反馈，继续下一步]

    L[预算阶段要求停止工作<br/>或重复检测达到阈值] --> M[Runner.finalize<br/>使用原始请求、预算提示和最近工具证据]
    M --> N[限制输入与输出<br/>ObservedModel.generate，tools 为空]
    N --> O[记录收尾结果<br/>返回 step_limit / budget_limit / no_progress]
    N -.ModelError.-> ME[保留原停止原因<br/>报告无法生成最终总结]
    M -.硬预算不足.-> HB([budget_limit])

    X[运行期间未在内部转成 ToolResult 的异常] --> Y{异常类别}
    Y -->|ContextError| CE([context_error])
    Y -->|ModelError| AE([api_error])
    Y -->|BudgetExceeded| HB
    Y -->|KeyboardInterrupt| CA([cancelled])
    Y -->|其他 Exception| HE([harness_error])
```

正常结束时 run_agent 将本次主模型、摘要与所有子 agent 的实际 used 汇总一次，再加上 session 旧累计用量，保存 Session.end。Runner 随后更新 RunState、保存 checkpoint、发出 run_finished。BudgetExceeded/KeyboardInterrupt/其他异常由 Runner 外层生成结果并保存会话；unknown_effect 则在进入循环前提前返回。

| RunResult.status | RunState.status |
| --- | --- |
| completed | completed |
| cancelled | cancelled |
| permission_required / unknown_effect | waiting_for_user |
| 其他停止原因 | failed |

收尾内容即使写得完整，status 仍保留停止原因；不能把收尾输出等同于任务通过验证。任意硬限制也不保证还能发起最后一次模型请求。

实现依据：[runner.py](../src/ajagent/harness/runner.py) 第 103、262、282 行、[hooks.py](../src/ajagent/harness/hooks.py) 第 26 行、[state.py](../src/ajagent/harness/state.py) 第 11 行、[loop.py](../src/ajagent/core/loop.py) 第 22、60 行。

## 6. 长任务、验收、反馈与延迟上下文交接

```mermaid
flowchart TD
    A[模型调用 plan_tasks<br/>定义目标、输出 paths、验收 checks] --> B[tasks.json 持久化计划<br/>一次只允许一个活动目标]
    B --> C[start_task<br/>主 agent 实现，按需委派独立分析]
    C --> D{阶段操作}
    D -->|checkpoint_task| E[checkpoint 保存工具参数中的 summary / next_step<br/>不调用额外摘要模型]
    D -->|verify_task| F[通过 Runner.execute 执行声明的 checks<br/>保存真实命令结果与文件哈希]
    F --> G{检查全部 exit 0、产物存在<br/>且检查前已存在文件内容未变?}
    G -->|否| H[failed 或工具错误<br/>保留活动目标，继续修复]
    H --> C
    G -->|是| I[目标 passed<br/>清空 active，保存验收证据]
    E --> J{compact_on_checkpoint?}
    I --> J
    J -->|是| K[设置 Runner.context_handoff = progress.context]
    K --> L[当前整个工具批次完成并落盘]
    L --> M[下一步 before_step 调用 progress.context]
    U[用户通过 ajagent-task feedback 提交反馈<br/>保存为 feedback 目录下的 JSON] -.此时读取.-> M
    M --> N[refresh 核对已通过项哈希<br/>变化或缺失则退回 pending]
    N --> O[读取任务状态、summary、当前请求<br/>加入未 rejected 的反馈，省略完整 evidence]
    O --> P[builder.summary = 脱敏任务上下文<br/>builder.covered = 当前全部历史组数<br/>清空 context_handoff]
    P --> Q[ContextBuilder 后续只发送交接状态、<br/>当前用户请求与交接后的新消息]
    J -->|否| Q2[继续使用常规 ContextBuilder 历史管理]
    Q --> R[选择下一个目标或准备完成]
    Q2 --> R
    R --> S{普通完成验证时<br/>所有目标仍为 passed?}
    S -->|否| C
    S -->|是| T[任务计划 hook 通过<br/>继续其他完成验证]
```

这里的 summary 是工作进度数据，包含模型已写下的摘要，不是第二次 LLM 压缩。`context()` 在下一次 before_step 才读取反馈，因此能带入 checkpoint 之后、交接之前新提交的反馈；不会每一步自动监听反馈目录。

`Feedback.list()` 读取 `.ajagent/tasks/<task_id>/feedback/*.json`，不是任意手写文本文件。pending 与 accepted 反馈均可进入任务上下文；只有显式 accept-feedback 才写入长期 Memory，rejected 反馈不进入交接上下文。

`--resume SESSION_ID` 恢复历史会话；`--task TASK_ID` 默认创建新会话，以持久任务状态重新开始上下文。TaskProgress.bind 还会关联任务记录的旧 run，继续检查未知副作用。新会话初次加载任务时，ToolManager 已将 context() 加入系统指令。

实现依据：[progress.py](../src/ajagent/tasks/progress.py) 第 28、97、152、160、203、210 行、[feedback.py](../src/ajagent/tasks/feedback.py) 第 20 行、[loader.py](../src/ajagent/tools/runtime/loader.py) 第 67 行、[Runner.before_step](../src/ajagent/harness/runner.py) 第 85 行。

## 7. 可选子 agent 与固定 research workflow

这两种机制分别是主循环里的一个工具和 CLI 选择的外层工作流。

```mermaid
flowchart TD
    A[默认 single 模式中的 delegate 调用] --> B[申请 subagent_slots<br/>创建独立 Session / Builder / Runner]
    B --> C[ChildBudget 受父预算约束<br/>共用预算锁与取消事件]
    C --> D[子 agent 运行相同 run_agent 循环<br/>仅使用允许的 read 工具及 propose_patch]
    D --> E[返回 report / evidence / proposal artifacts<br/>子会话原始历史留在自己的 session]
    E --> F[父 agent 根据结果继续决策<br/>通过 apply_proposal 等串行写入]

    G[CLI 选择 research workflow] --> H[Coordinator.run]
    H --> I[researcher: 收集带 evidence ID 的事实]
    I --> J[writer: 生成带引用草稿]
    J --> K[reviewer: 检查草稿]
    K --> L{审阅通过?}
    L -->|是| M[协调器保存 final.md<br/>workflow completed]
    L -->|否且可修订| N[reviser: 根据问题生成新版本]
    N --> K
    L -->|否且次数耗尽| O[保存 partial 结果与问题]
```

- delegate 默认禁止继续嵌套委派；子 agent 只能产生分析或 proposal artifact，主 agent 才能修改工作区。多个同批 delegate 调用可并行，used 向父 Runner 汇总。
- research 模式的每个角色也复用 Runner。节点有独立的结构化输出/证据校验及有限重试，外层协调器负责版本与发布。该模式不经过父 Runner.run 的单主循环入口，CLI 明确不支持 research 配合 --resume/--task。

实现依据：[delegation/agent.py](../src/ajagent/tools/delegation/agent.py) 第 18 行、[coordinator.py](../src/ajagent/multi_agent/coordinator.py) 第 30、74 行、[cli.py](../src/ajagent/cli.py) 第 83、140 行。

## 8. 持久化数据与恢复职责

| 路径 | 内容与用途 |
| --- | --- |
| `.ajagent/sessions/<session_id>.jsonl` | 完整可重放消息组、授权、context 摘要、task 绑定、run 关联与累计 usage；常规事件可缓冲，完整消息组立即 flush。 |
| `.ajagent/runs/<run_id>/checkpoint.json` | state、completed_step、pending、changed、groups 数量、session usage、evidence；通过临时文件替换。恢复只取需要的 changed/evidence/pending，不续用旧运行预算或旧步数。 |
| `.ajagent/runs/<run_id>/events.jsonl` | 模型、工具、权限、预算阶段、压缩、验证与结束事件；trace 读取它而不重新执行。 |
| `.ajagent/runs/<run_id>/artifacts/` | 超长工具结果、子任务 report/proposal、可选 workflow 版本；read_artifact 可按 offset/length 读取受限片段。 |
| `.ajagent/tasks/<task_id>/tasks.json` | 长任务唯一权威状态：目标、active、summary、next_step、状态、验收证据与文件哈希。 |
| `.ajagent/tasks/<task_id>/checkpoints/` | 长任务每个 revision 的快照；区别于 run 的副作用 checkpoint。 |
| `.ajagent/tasks/<task_id>/progress.md` | 供人查看的任务进度投影。 |
| `.ajagent/tasks/<task_id>/feedback/` | 用户反馈 JSON；接受后才另写长期记忆。 |

两种预算需要分清：ContextBuilder 的预算约束单次输入能否放进模型上下文；Budget 的限额约束整个 run 和它的子 agent 的资源使用。二者的超限分支分别可能产生 context_error 与 budget_limit。
