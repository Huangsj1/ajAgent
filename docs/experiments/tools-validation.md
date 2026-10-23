# 统一工具系统与并行任务验证

验证日期：2026-10-22。当前使用 ToolManager 单一装配入口，已移除 extensions 分类和零散的 RAG/browser/MCP CLI 开关。操作说明见 [Tools README](../../src/ajagent/tools/README.md)；协议系统说明见 [MCP README](../../src/ajagent/tools/mcp/README.md)。

## 可复现配置与命令

主配置集中选择能力，`disabled` 可以排除具体工具：

```toml
[tools]
enabled = ["files", "database", "execution", "web", "skills", "delegation", "artifacts"]
disabled = []
skills_path = "../skills"
mcp_config = "mcp.local.toml"
```

需要 RAG 时在列表加 `retrieval`，来源、知识库路径、重排等继续配置在 `[rag]`。需要 MCP 时加 `mcp`，编辑 `config/mcp.local.toml`；示例服务提供 `mcp_demo_add`。需要浏览器时加 `browser`，安装 browser extra 与 Chromium。

```bash
uv sync --all-extras
export PLAYWRIGHT_BROWSERS_PATH=0
uv run --all-extras playwright install chromium
uv run --all-extras ajagent '/skill research 总结 knowledge/11-evidence.md，给出来源'
uv run --all-extras ajagent '根据 available skills，按需加载适合的 skill 后回答'
uv run --all-extras ajagent '同一步委派两个带 contract 的子任务，分别总结 knowledge/08-memory.md 和 knowledge/11-evidence.md'
```

浏览器本地 fixture：一个终端运行 `uv run python -m http.server 8765 --bind 127.0.0.1 --directory examples/browser_fixture`，另一个用普通 ajagent 命令要求打开地址、点击 `#reveal`、读取 `#result`。无需再传工具启用开关。

## 回归结果

完整回归：

```bash
PLAYWRIGHT_BROWSERS_PATH=0 uv run --all-extras pytest -q
# 203 passed
```

Chromium 在本机 macOS 沙箱外的获准测试进程中运行，本地动态页面与 browser/MCP 共存测试没有跳过。MCP 的 HTTP 测试使用真正的本地 Streamable HTTP 服务模拟远程端点，未连接用户的生产远程 server。

| 范围 | 实际验证 |
| --- | --- |
| 统一入口 | 指定工具组、单工具排除、错误配置；CLI 不再分散注册工具 |
| Skill | 初始 prompt 只有 metadata；load_skill 返回正文；手动与模型加载均不能扩大权限 |
| Read 并行 | Barrier fixture 证明两个 read 同时执行；结果按原 call 顺序回填；单项错误不丢其他结果 |
| 串行屏障 | write/execute 等待前面 reads 完成；后续 reads 在写完成后启动 |
| 共享预算 | 正在进行的请求预留额度，父子同时检查；模型实际费用结算与预约释放；父子用量不重复累计 |
| 并行子任务 | 两个独立模型适配器、独立上下文/预算；contract 校验、无递归、继承 deny/hooks、失败与取消 |
| Single writer | 子任务仅产出 diff/草稿 artifact；主 agent 审批后应用；路径越界、过期基线、无效 Python 语法均拒绝；未批准不写入 |
| stdio MCP | 真实服务连续调用 PID 相同、调用计数递增；多 server 名称隔离；显式重连重新创建进程；关闭后线程退出 |
| HTTP MCP | 真实 HTTP 服务连续调用成功，同一服务状态持续存在；默认 execute 权限 |
| MCP 配置 | 环境变量凭据展开、字段校验、缺失服务超时；mcp.local.toml 被文件工具排除 |
| 既有功能 | 模型协议、RAG/索引、上下文/记忆、会话恢复、网页抓取、脚本超时、权限/验证、浏览器交互等回归通过 |

相关测试：`test_tool_runtime.py`、`test_mcp_system.py`、`test_delegation.py`、`test_capabilities.py`，以及既有 harness/model/RAG 测试。

## 双厂商真实并行冒烟

提示要求同一步发出两个 delegate，分别读取上述两个 knowledge 文档，填写完整 task contract，不生成补丁。两个 provider 都实际返回同一步两个工具调用，两个 child 状态均为 completed。

| 模型 | 子任务启动时间间隔 | 首个结束前两个任务均已启动 | session total input/output | 父级 last_usage input/output |
| --- | --- | --- | --- | --- |
| deepseek-flash | 约 1.3 ms | 是 | 11705 / 1702 | 3954 / 394 |
| gpt-6-luna | 约 0.5 ms | 是 | 7026 / 761 | 2328 / 210 |

本地 run ID：`86e1380059e6490e8f52911d1a15db44`、`e3365891e86e484494ca0ffd32dfc0c4`。用各 run 与 child run 的 model_finished usage 求和，分别等于父 run_finished 的 total usage。时间间隔来自本机事件日志，仅证明这次实际重叠执行，不作为性能基准或加速比。

新提案应用流程通过离线真实文件写入测试，未让真实模型修改本项目代码。基线与语法检查可自动化，逻辑冲突仍需主 agent 结合任务目标审阅；单有 review 字符串不能证明语义正确。完整 research → write → review → revise coordinator 与量化质量评测留在 Stage 7。

## 配置目录与测试服务迁移

完整回归：206 passed。新增入口 include 合并、递归相对路径、循环引用、缺失分片与完整模板初始化测试。真实 stdio/HTTP 测试共用根目录 `mcp/` 服务，本地 `config/mcp.local.toml` 的 add 调用也返回 5。各 local 分片通过 Git ignore 检查，模型和 Tavily 凭据只做存在性检查，不输出值。
