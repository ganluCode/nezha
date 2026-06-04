# Claude Code SDK 调用点梳理

本文档记录当前项目中所有直接调用 `claude-code-sdk` 的位置，说明每处调用的输入、输出和调用意图。后续支持 Codex 或其他 runtime 时，应优先以本文档作为拆分边界。

## 总览

当前直接调用 `claude-code-sdk` 的代码集中在 4 类场景：

| 场景 | 文件 | 作用 |
|------|------|------|
| 主执行 runtime | `src/nezha/engine.py` | 封装 Claude Code SDK，统一输出 `SessionEvent` / `SessionResult` |
| session 子进程模板 | `src/nezha/pipeline/session.py` | 在隔离子进程中调用 `engine.py`，承载 single/multi/vibe 主执行链路 |
| AI Judge | `src/nezha/executor.py` | feature 失败后，用轻量 Claude 调用判断下一个 feature 是否可继续 |
| Direct API 的 Claude 快捷路径 | `src/nezha/pipeline/direct_api.py` | direct 模式下，Claude 模型复用 Claude Code 登录态 |
| Heartbeat | `src/nezha/heartbeat.py` | 定时 ping Claude 模型，复用 Claude Code 登录态 |

另外，`nezha code` 启动的是 `claude` CLI，不是 `claude-code-sdk`，本文不归入 SDK 调用点。

## 1. `src/nezha/engine.py`：主 SDK 封装层

### 1.1 SDK import 与消息类型绑定

代码位置：

- `src/nezha/engine.py:8`
- `src/nezha/engine.py:9-15`

输入：

- 无运行时输入，模块加载时 import：
  - `ClaudeCodeOptions`
  - `query`
  - `AssistantMessage`
  - `HookMatcher`
  - `ResultMessage`
  - `UserMessage`

输出：

- Python 符号绑定，供本模块后续构造 options 和解析 SDK 消息流使用。

调用意图：

- 将 Claude Code SDK 作为当前默认 LLM runtime。
- 把 SDK 原始消息类型转换为 nezha 内部统一事件模型。

重构含义：

- 这是最核心的 Claude Code 耦合点。支持 Codex 时，应把这部分迁到类似 `runtime/claude_code.py` 的 adapter 中。

### 1.2 SDK 内部 parser monkey-patch

代码位置：

- `src/nezha/engine.py:24-41`

输入：

- Claude Code SDK 内部模块：
  - `claude_code_sdk._internal.message_parser`
  - `claude_code_sdk._internal.client`
- SDK 输出的原始 JSON 消息。

输出：

- 替换后的 `_mp.parse_message`
- 替换后的 `_client.parse_message`

调用意图：

- SDK 旧版本遇到未知消息类型会抛 `MessageParseError`。
- Claude Code CLI 会输出一些 SDK 未定义的新事件，例如 `rate_limit_event`。
- monkey-patch 的目标是：未知消息返回 `None`，让主循环跳过，避免 session 因非核心事件崩掉。

注意：

- `rate_limit_event` 是信息性事件，不等价于真正限流。
- 真正限流目前在 `ResultMessage.is_error` + 错误文本中识别。

### 1.3 `build_options()`：构造 `ClaudeCodeOptions`

代码位置：

- `src/nezha/engine.py:64-96`

输入：

- `agent_config: AgentConfig`
  - `agent_config.engine.model`
  - `agent_config.engine.max_turns`
  - `agent_config.engine.tools`
  - `agent_config.engine.mcp_servers`
- `workspace: Path`
  - 实际传入时通常是 LLM 的 cwd；coding agent 通常是 target 仓库，非 coding agent 通常是 feature workspace。
- `security_hook`
  - 来自 `pipeline/security.py` 的 Claude Code `PreToolUse` hook。
- `env`
  - executor/env、agent/env、model_map env 合并后的环境变量。
- `extra_mcp_servers`
  - executor 级全局 MCP 配置。

输出：

- `ClaudeCodeOptions`
  - `model`
  - `max_turns`
  - `allowed_tools`
  - `mcp_servers`
  - `cwd`
  - `hooks`
  - `permission_mode="bypassPermissions"`
  - `env`

调用意图：

- 把 nezha 的 Agent YAML 配置映射成 Claude Code SDK 的运行参数。
- 合并 executor 级 MCP 和 agent 级 MCP，agent 级覆盖全局。
- 注入安全 hook，只拦截 Bash 工具。

重构含义：

- 这里包含 Claude 专属能力：`HookMatcher`、`allowed_tools`、`mcp_servers`、`permission_mode`。
- Codex CLI runtime 不应强行模拟这些字段，应使用自己的 sandbox/approval 参数。

### 1.4 `run_session()`：执行 SDK query 并转换事件

代码位置：

- `src/nezha/engine.py:99-203`
- 核心 SDK 调用：`src/nezha/engine.py:114`

输入：

- `prompt: str`
  - 已渲染好的完整 prompt，包含 input 文件、project context、AGENTS/CLAUDE 知识、agent-context、DAG context 等。
- `options: ClaudeCodeOptions`
  - 来自 `build_options()`。

输出：

- 异步生成器，yield 两类内部对象：
  - `SessionEvent`
    - `thinking`
    - `tool_call`
    - `tool_result`
  - `SessionResult`
    - `status`
    - `duration_ms`
    - `num_turns`
    - `cost_usd`
    - `input_tokens`
    - `output_tokens`
    - `result_text`
    - `error`

SDK 消息映射：

| SDK 消息 | SDK block | nezha 输出 |
|----------|-----------|------------|
| `AssistantMessage` | `TextBlock` | `SessionEvent(event_type="thinking")` |
| `AssistantMessage` | `ToolUseBlock` | `SessionEvent(event_type="tool_call")` |
| `AssistantMessage` | `ThinkingBlock` | `SessionEvent(event_type="thinking")` |
| `UserMessage` | `ToolResultBlock` | `SessionEvent(event_type="tool_result")` |
| `ResultMessage` | N/A | `SessionResult` |

调用意图：

- 这是 Claude Code SDK 到 nezha 内部事件系统的主要适配层。
- 上层 DAG/Executor 不直接理解 SDK 消息，只依赖 `SessionEvent` / `SessionResult`。

错误状态约定：

- `msg.is_error == False` → `status="completed"`
- 普通错误 → `status="error"`
- 不可恢复错误 → `status="rate_limited"`
  - 当前包含关键词：
    - `rate limit`
    - `rate_limit`
    - `too many requests`
    - `overloaded`
    - `429`
    - `529`
    - `authentication_error`
    - `invalid authentication`
    - `401`

注意：

- `rate_limited` 这个状态名目前也承载了认证失败等“后台继续执行无意义”的不可恢复错误，语义上未来可以改名为 `unrecoverable` 或拆成更细状态。

## 2. `src/nezha/pipeline/session.py`：主执行链路的间接调用点

`session.py` 本身不直接 import `claude_code_sdk`，但它通过子进程模板 import `nezha.engine`，最终触发 SDK 调用。它是业务主链路接入 Claude SDK 的入口。

### 2.1 `_SUBPROCESS_RUNNER`：隔离子进程中的 SDK 调用模板

代码位置：

- 模板定义：`src/nezha/pipeline/session.py:196`
- 子进程 import `build_options/run_session`：`src/nezha/pipeline/session.py:202-203`
- 构造 options：`src/nezha/pipeline/session.py:305-313`
- 调用 session：`src/nezha/pipeline/session.py:315-371`
- 写结果文件：`src/nezha/pipeline/session.py:373-385`

输入：

- 父进程通过 `.format()` 注入到模板中的参数：
  - `project_root`
  - `executor_config_path`
  - `agent_config_path`
  - `workspace`
  - `cwd`
  - `project_dir`
  - `agent_workspace`
  - `prompts_dir`
  - `prompt_path`
  - `prompt_key`
  - `model_override`
- 子进程内从文件重新加载：
  - `executor.yaml`
  - `agents/<agent>.yaml`
- prompt 相关输入：
  - input 文件
  - prompt 模板或 compose 配置
  - `CLAUDE.md` / `AGENTS.md` 知识
  - `agent-context.md`
  - `workspace/project/` project context
- security 输入：
  - `agent_config.engine.security.allowed_commands`
- env 输入：
  - `executor_config.env`
  - `agent_config.engine.env`

输出：

- 控制台输出：
  - thinking 文本
  - tool 调用摘要
  - tool result 摘要
- 文件输出：
  - `.session_manifest.json`
  - `.session_result.json`

调用意图：

- 用独立 Python 子进程隔离每次 Claude Code SDK 调用，避免 SDK anyio cancel scope 污染父进程 event loop。
- 将 SDK 异步事件流压缩成 `.session_result.json`，父进程读取后继续 DAG/Executor 流程。

重构含义：

- 支持 Codex CLI 时，这里是最适合插 runtime dispatcher 的地方。
- 目标不是移除子进程，而是让子进程内可选择 `claude_code` 或 `codex_cli` runtime。

### 2.2 `run_single_round()`：single round 的 SDK 间接入口

代码位置：

- `src/nezha/pipeline/session.py:120-188`
- 调用 `_run_isolated_session()`：`src/nezha/pipeline/session.py:174-187`

输入：

- `executor_config`
- `agent_config`
- `workspace`
- `env`
- `target`
- `project_dir`
- `agent_workspace`
- `mode`
- `base_dir`

输出：

- `SessionResult`

调用意图：

- 运行一个 agent 的单轮任务。
- 实际执行由 `_run_isolated_session()` 创建子进程，子进程内走 `_SUBPROCESS_RUNNER` → `engine.run_session()` → `claude_code_sdk.query()`。

### 2.3 `run_multi_round()`：DAG multi-round 的 SDK 间接入口

代码位置：

- `src/nezha/pipeline/session.py:597-859`
- initializer 调用 `_run_isolated_session()`：`src/nezha/pipeline/session.py:716-728`
- DAG 单 task session 调用 `_run_isolated_session()`：`src/nezha/pipeline/session.py:790-811`
- DAGEngine 注入 `run_session_fn`：`src/nezha/pipeline/session.py:831-842`

输入：

- `executor_config`
- `agent_config`
- `workspace`
- `max_iterations`
- `env`
- `target`
- `project_dir`
- `agent_workspace`
- `base_dir`
- `skip_planner`
- `task_list.json`
- `model_map`

输出：

- `tuple[list[SessionResult], DAGExecutionResult | None]`

调用意图：

- 多轮 DAG 执行。
- 每个 DAG task 都通过 `_run_one_session()` 进入 `_run_isolated_session()`。
- `model_map` 在 DAG 层解析后，通过 `model_override/env_override` 传入单次 session，最终落到 Claude SDK 的 `model/env`。

重构含义：

- Codex 支持不能只改 `engine.py`，还要让 `_run_one_session()` 能根据 runtime 分派到不同 runner。

### 2.4 `_VIBE_SUBPROCESS_RUNNER`：VibeCoding 的 SDK 间接入口

代码位置：

- 模板 import：`src/nezha/pipeline/session.py:874`
- 模板内逻辑与 `_SUBPROCESS_RUNNER` 同构。

输入：

- vibe prompt
- handoff context
- agent config / executor config
- target / workspace / project context

输出：

- `.session_result.json`
- 控制台事件流

调用意图：

- 给交互式 VibeCoding 模式提供 Claude Code SDK 子进程执行能力。

注意：

- Codex 第一版如果只做无人值守 `nezha run`，可以暂时不改 vibe。

## 3. `src/nezha/executor.py`：AI Judge 的 SDK 调用

### 3.1 `_judge_call_sdk()`

代码位置：

- `src/nezha/executor.py:508-526`
- SDK import：`src/nezha/executor.py:510-511`
- options 构造：`src/nezha/executor.py:513-518`
- SDK query：`src/nezha/executor.py:520`

输入：

- `prompt: str`
  - `_ai_judge_continue()` 构造的判断 prompt。
  - 问题是失败 feature 后，下一个 feature 是否可独立继续。
- `model: str`
  - 通常来自 `model_map.low`，没有时 fallback 到 `scheduler.judge_model`。
- `env: dict`
  - executor env、judge_env、model_map low env 合并后的环境变量。

输出：

- `result_text: str`
  - SDK `ResultMessage.result`
  - 上层只关心其中是否包含 `CONTINUE`。

调用意图：

- 用很便宜/轻量的 Claude 模型做调度决策。
- 对 Claude 模型复用 Claude Code 登录态，不要求用户额外配置 `ANTHROPIC_API_KEY`。

注意：

- 此处没有使用 `engine.run_session()`，而是直接调用 SDK。
- 它只读取 `ResultMessage`，不关心 tool events、tokens、cost。

重构含义：

- 未来应改成通用 `JudgeRuntime` 或复用 runtime adapter 的 `run_text_once()` 能力。

## 4. `src/nezha/pipeline/direct_api.py`：direct 模式下的 Claude SDK 快捷路径

### 4.1 `_call_sdk()`

代码位置：

- `src/nezha/pipeline/direct_api.py:367-385`
- monkey-patch 触发：`src/nezha/pipeline/direct_api.py:369`
- SDK import：`src/nezha/pipeline/direct_api.py:370`
- options 构造：`src/nezha/pipeline/direct_api.py:372-377`
- SDK query：`src/nezha/pipeline/direct_api.py:379`

输入：

- `prompt: str`
  - direct 模式组装后的完整 prompt。
- `model: str`
  - direct agent 的 `engine.model`。
- `env: dict`
  - executor env + agent env。

输出：

- `result_text: str`
  - SDK `ResultMessage.result`

调用意图：

- direct 模式原本是“无工具、轻量 prompt → text”的 API 路径。
- 但当模型是 Claude 时，用 Claude Code SDK 可以复用本机 Claude Code 登录态，避免用户重复配置 API key。

注意：

- 这是 direct 模式里的一个 Claude 分支，不是主 coding session。
- 它不产出 `SessionEvent`，只返回文本。

重构含义：

- 支持 Codex 后，direct 模式可能需要拆成：
  - provider API direct call
  - runtime text-only call
  - CLI runtime text-only call

## 5. `src/nezha/heartbeat.py`：Heartbeat 的 SDK 调用

### 5.1 `_ping_sdk()`

代码位置：

- `src/nezha/heartbeat.py:41-57`
- monkey-patch 触发：`src/nezha/heartbeat.py:43`
- SDK import：`src/nezha/heartbeat.py:44`
- options 构造：`src/nezha/heartbeat.py:46-50`
- SDK query：`src/nezha/heartbeat.py:52`

输入：

- `model: str`
  - `executor.yaml` 的 `heartbeat.models[].model`
- `env: dict`
  - `heartbeat.models[].env`
- 固定 prompt：
  - `_HEARTBEAT_PROMPT = "hi"`

输出：

- `"ok"` 或异常字符串。

调用意图：

- 定时对模型发送极小请求，验证模型/登录态可用。
- Claude 模型通过 Claude Code SDK 复用登录态。

注意：

- 这里不使用 `engine.run_session()`，只是等待第一个 `ResultMessage`。
- 此处 import `nezha.engine` 的唯一目的，是触发 `rate_limit_event` monkey-patch。

重构含义：

- Heartbeat 后续也应复用 runtime adapter 的 `ping()` / `run_text_once()`，否则每新增一个 runtime 都要在 heartbeat 中重复写一套判断。

## 6. 非 SDK 但相关的 Claude 入口

以下位置与 Claude Code 强相关，但不是 `claude-code-sdk` 调用：

| 入口 | 文件 | 说明 |
|------|------|------|
| `nezha code` | `src/nezha/__main__.py` / `src/nezha/interface/cli.py` | 启动 `claude` CLI，进入交互式 Claude Code |
| `nezha init` Claude 配置生成 | `src/nezha/interface/cli.py` | 生成 `.claude/skills`、Claude 配置和项目说明 |
| `pipeline/security.py` | `src/nezha/pipeline/security.py` | 生成 Claude Code `PreToolUse` hook 所需的安全检查函数 |
| `.claude/skills` 模板 | `src/nezha/interface/cli.py` 内置字符串 | Claude Code skill 文件内容 |

这些不是 SDK 调用，但会影响 Codex 支持时的产品形态。Codex runtime 重构时，不应只处理 `engine.py`，也要规划 init/skills/code 的对应关系。

## 7. 后续重构建议

建议把 SDK 调用点收敛为统一 runtime adapter：

```text
src/nezha/runtime/
  types.py          # SessionEvent / SessionResult
  base.py           # Runtime Protocol
  claude_code.py    # 当前 engine.py 的主体逻辑
  codex_cli.py      # 未来 Codex CLI runtime
```

第一步应先保持行为不变，只把 Claude SDK 调用封装起来：

1. `SessionEvent` / `SessionResult` 从 `engine.py` 移到中立模块。
2. `engine.py` 变成兼容导出层，避免一次性大改调用方。
3. 新建 `ClaudeCodeRuntime`，承接 `build_options()` / `run_session()`。
4. `session.py` 子进程模板通过 runtime factory 分派，而不是硬 import `build_options/run_session`。
5. AI Judge、direct、heartbeat 后续复用 runtime 的 text-only / ping 能力，减少重复 SDK 调用。

这样做的收益是：Codex CLI 接入时，只需要实现同一份 runtime 协议，不需要把 DAG、FeatureQueue、Scheduler、Guard、EventBus 全部翻一遍。
