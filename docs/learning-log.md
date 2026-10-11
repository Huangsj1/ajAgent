# Learning Log

## Stage 0 — 2026-10-11

**已完成：** 使用已安装的 uv 0.7.21 和 Python 3.13.7 创建 `.venv`；建立依赖声明和锁文件；安装 httpx、pytest；建立空凭据配置模板与本地配置；更新阶段文档。

**学到的内容：** `.venv` 是实际 Python 环境，`pyproject.toml` 声明项目依赖范围，`uv.lock` 锁定解析结果。`uv run` 使用项目环境，无需手动激活。

**遇到的问题：** 当前执行环境无法访问默认 uv 缓存目录。将缓存显式设为项目内 `.uv-cache/` 并加入忽略规则后，环境创建成功。

**设计取舍：** 先使用 httpx 直接发送 HTTP 请求，便于下一阶段观察消息协议。配置模板先确定结构，Stage 1 内联必要加载，Stage 2 再提取统一配置模块，不提前建设框架。

**配置边界：** key 和 model 初始为空；没有读取用户凭据或发送模型请求。Stage 0 完成的是环境与配置约定，优先级合并、日志规则和请求行为待后续实现。

**验证：** `uv sync --locked`、依赖导入、TOML 解析、Git 忽略规则检查。当前没有 agent 功能测试。

**下一步：** 用户填写 `config.local.toml`，然后实现 Stage 1 的最小工具调用循环。


## Stage 1 — 2026-10-11

**已完成：** 122 行非空代码的独立示例，含配置读取、两个工具、HTTP 请求、消息回填、终止状态和英文注释。使用 httpx，没有 Agent SDK。

**关键理解：** 模型返回的 tool_calls 只是请求。Python 执行工具，先保存 assistant 消息，再按每个 call_id 回填结果，模型才能在下一轮据此回答。一轮可能包含多个工具请求。

**设计取舍：** 先用非流式、关闭 thinking 的请求简化观察；保留 assistant 原始消息，不打印 reasoning。工具参数错误反馈给模型，网络错误直接结束。配置和循环暂放同一文件，Stage 2 再提取。

**离线验证：** `uv run pytest -q`：21 passed。覆盖直接回答、查找后计算、多调用与错误结果、非法参数/JSON、空结果、8 步停止、空输出、拒绝、截断、配置覆盖、HTTP/超时错误不泄露响应体。

**真实演示：** 模型 `deepseek-flash`；命令 `uv run python examples/minimal_agent.py "请先用 lookup_note 查询 study 笔记，再用 add 计算阅读与练习一共需要多少分钟，简洁回答。"`。观察到 Step 1 lookup_note 返回 25/35；Step 2 add 返回 60；第三次模型请求返回最终答案“共 60 分钟”，状态 completed、退出码 0。未输出 key，未修改用户本地配置。

**失败场景：** 通过假模型连续返回工具调用，在第 8 次请求后得到 step_limit；不是 completed，也不会额外发送第 9 次请求。HTTP 401 与超时通过离线传输模拟，错误文本不包含响应体或密钥。

**下一步：** Stage 2 提取模型适配、配置与工具层；继续保留此教学脚本。


## Stage 2 — 2026-10-11

**已完成：** src 包、ajagent CLI、双厂商适配、四个只读工具、schema 校验、错误隔离、有界重试、输出限制和重复批次停止。保留用户编辑过的 Stage 1 脚本。

**学到的内容：** 可以统一模型返回的语义，但不能把不同 wire format 简单混在一起。OpenAI output item 的 id 不等于函数调用 call_id；续接必须保存原始 reasoning items。DeepSeek 保留完整 assistant 消息。

**设计取舍：** 共用 HTTP 重试，不重跑 loop；先用 Python 字面量文件搜索减少系统依赖；测试集中放在 test_stage2.py，规模扩大后再按模块拆分。使用 jsonschema 校验参数，不建设工具框架。

**离线验证：** `uv run pytest -q`：54 passed。涵盖两家适配器、同轮失败/成功工具、续接字段、HTTP/网络重试、配置覆盖、工作目录/密钥文件/链接限制、SQL 只读与超时、空结果、结果截断及无进展终止。

**真实验证：** 分别执行 `uv run ajagent --provider deepseek --workspace tests/fixtures/workspace "先用 search_files 搜索 study，再用 read_file 读取找到的文件。根据文件回答学习总时长，标注文件名和行号。"` 及同命令 `--provider openai`。

| 模型 | 工具轨迹 | 结果 | 本次返回的用量 |
| --- | --- | --- | --- |
| deepseek-flash | search_files → read_file | completed；60 分钟；plan.md 第 4 行 | input 2598 / output 200 |
| gpt-6-luna | search_files → read_file | completed；60 分钟；plan.md 第 4 行 | input 1484 / output 61 |

用量只记录本次演示，不作为性能比较或费用推算。未修改本地凭据，未打印原始 API 消息。真实演示之后只调整了结果长度边界与非有限 SQL 数值序列化，均通过离线回归。

**失败与边界：** 模拟 401 只请求一次；429/5xx/网络超时最多三次尝试；工具执行不随 HTTP 重试重复。三个相同批次无新结果时 no_progress，改变查询但无限调用时受 max_steps 限制。

**下一步：** Stage 3 引入 RAG 索引与严格来源 ID 校验。目前的路径/行号引用仍需用户核对语义。
