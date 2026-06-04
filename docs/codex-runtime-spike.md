# Codex Runtime Spike

> 目的：在正式重构前，验证 Codex CLI 是否具备作为 nezha runtime 的稳定调用面，并据此确定 runtime 抽象边界。

## 背景

当前 nezha 的默认执行 runtime 是 Claude Code SDK：

```text
session.py → 子进程模板 → engine.py → claude-code-sdk query()
```

这条链路已经能稳定支撑：

- single\_round
- multi\_round DAG task session
- tool\_call / tool\_result 事件
- cost / token 统计
- MCP 注入
- PreToolUse 安全 hook

但这些能力并不是所有 coding agent runtime 都会完整提供。Codex CLI 更像一个独立命令行 agent，天然适合 nezha 的子进程隔离模型，但它的事件、权限、认证、上下文加载方式都需要先验证。

本 spike 不改主代码，只验证 Codex CLI 的行为，并为后续 runtime 抽象提供依据。

## 抽象原则

不要设计一个要求 Claude Code 和 Codex 完全等价的接口。正确方向是：

1. **公共接口只覆盖 nezha 主流程必须依赖的能力**
2. **差异能力用 capability flags 或可选 Protocol 表达**
3. **上层根据 capability 降级，而不是强行模拟不存在的能力**

类似 Java 中：

```text
Runtime               # 公共接口：能跑一次 session，返回结果
ToolEventRuntime      # 可选接口：能暴露工具调用事件
CostTrackingRuntime   # 可选接口：能暴露 token/cost
SandboxRuntime        # 可选接口：能配置沙盒/审批策略
McpRuntime            # 可选接口：能注入 MCP server
```

Claude Code Runtime 可以实现更多接口；Codex CLI Runtime 第一版只需要实现主执行所需的最小集合。

## 候选接口草案

### 公共 runtime 接口

```python
class AgentRuntime(Protocol):
    name: str
    capabilities: RuntimeCapabilities

    async def run_session(
        self,
        prompt: str,
        cwd: Path,
        model: str,
        env: dict[str, str],
        timeout: int,
        context: RuntimeContext,
    ) -> AsyncGenerator[SessionEvent | SessionResult, None]:
        ...
```

公共输入：

| 字段        | 说明                                                       |
| --------- | -------------------------------------------------------- |
| `prompt`  | nezha 已渲染好的完整 prompt                                     |
| `cwd`     | agent 实际操作目录，coding agent 通常是 target repo                |
| `model`   | 本次 session 使用的模型，支持 model\_map override                  |
| `env`     | executor/agent/model\_map 合并后的环境变量                       |
| `timeout` | session 超时时间                                             |
| `context` | 运行时上下文，如 workspace、project\_root、agent\_name、prompt\_key |

公共输出：

| 输出              | 说明                           |
| --------------- | ---------------------------- |
| `SessionEvent`  | 可选事件流，runtime 没有事件时可以不 yield |
| `SessionResult` | 必须输出，用于 DAG/Executor 判断成功失败  |

### RuntimeCapabilities

```python
@dataclass
class RuntimeCapabilities:
    tool_events: bool = False
    cost_tracking: bool = False
    token_tracking: bool = False
    pre_tool_hook: bool = False
    sandbox: bool = False
    mcp: bool = False
    output_schema: bool = False
    resume: bool = False
```

建议初始能力表：

| 能力             | Claude Code SDK     | Codex CLI   | 备注                                                   |
| -------------- | ------------------- | ----------- | ---------------------------------------------------- |
| prompt 输入      | yes                 | yes         | `query(prompt=...)` vs `codex exec [PROMPT]` / stdin |
| cwd            | yes                 | yes         | `cwd` vs `--cd`                                      |
| model          | yes                 | yes         | `model` vs `--model`                                 |
| tool events    | yes                 | unknown     | 需验证 `--json` JSONL                                   |
| cost tracking  | yes                 | likely no   | Codex CLI help 未显示                                   |
| token tracking | yes                 | unknown     | 需验证 JSONL                                            |
| pre tool hook  | yes                 | no          | Codex 用 sandbox/approval 替代                          |
| sandbox        | no/permission\_mode | yes         | `--sandbox`                                          |
| MCP            | yes                 | yes/unknown | Codex CLI 有 `codex mcp`，session 注入方式需验证              |
| output schema  | no                  | yes         | `--output-schema`                                    |
| resume         | SDK 不明显             | yes         | `codex exec resume`，是否适合 DAG 需另看                     |

## Codex CLI 已知调用面

本机 `codex exec --help` 和 `codex --help` 显示的关键参数：

```text
codex exec [OPTIONS] [PROMPT]

--model <MODEL>
--sandbox <read-only|workspace-write|danger-full-access>
--cd <DIR>
--add-dir <DIR>
--skip-git-repo-check
--ephemeral
--ignore-user-config
--ignore-rules
--output-schema <FILE>
--json
--output-last-message <FILE>

codex [GLOBAL OPTIONS] exec ...

--ask-for-approval <untrusted|on-failure|on-request|never>
```

注意：在 Codex CLI 0.130.0 中，`--ask-for-approval` 是顶层参数，需要写在 `exec` 前面：

```bash
codex --ask-for-approval never exec ...
```

不要写成：

```bash
codex exec --ask-for-approval never ...
```

Spike 命令默认加上：

```bash
--ignore-user-config --ignore-rules --ephemeral
```

原因：

- `--ignore-user-config`：避免加载用户全局 Codex 配置里的 MCP / profile / plugin 设置，减少远程 MCP 初始化失败对 spike 的干扰；认证仍使用 `CODEX_HOME`。
- `--ignore-rules`：避免用户或项目 `.rules` 影响实验结果。
- `--ephemeral`：不持久化 Codex session 文件，避免产生无关会话记录。

这些参数足够支撑一个最小 runtime：

```text
prompt → codex exec --json --output-last-message → SessionResult
```

但还不能直接确认：

- JSONL 每种事件的结构
- tool\_call/tool\_result 是否能稳定映射
- error event 结构
- 退出码和最终消息文件的关系
- timeout / SIGTERM 后是否能清理干净
- MCP 是否能 per-session 配置

## Spike 验证项

### 1. 基础成功路径

目标：确认 prompt 输入、cwd、最终消息文件、退出码。

命令：

```bash
mkdir -p /tmp/nezha-codex-spike
cd /tmp/nezha-codex-spike
git init
codex --ask-for-approval never exec \
  --cd /tmp/nezha-codex-spike \
  --model gpt-5.3-codex \
  --sandbox workspace-write \
  --ignore-user-config \
  --ignore-rules \
  --ephemeral \
  --json \
  --output-last-message /tmp/nezha-codex-spike/.codex-last-message.txt \
  "Create a hello.txt file containing exactly: hello from codex"
echo $?
cat /tmp/nezha-codex-spike/.codex-last-message.txt
ls -la /tmp/nezha-codex-spike
```

观察：

- stdout 是否为 JSONL
- return code 是否为 0
- `.codex-last-message.txt` 是否存在
- 文件是否真的写入 cwd

已验证结果（2026-05-28，Codex CLI 0.130.0）：

- return code：`0`
- stdout：JSONL，可解析
- `.codex-last-message.txt`：存在，内容为最终 agent message
- cwd 写入：成功创建 `hello.txt`
- usage：`turn.completed` 事件包含 `usage`
- 示例事件：

```json
{"type":"thread.started","thread_id":"019e6d4d-7e3e-7742-963c-2cda006ffe9b"}
{"type":"turn.started"}
{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"我将直接在当前目录创建 `hello.txt`，并写入你指定的精确内容，然后确认文件内容。"}}
{"type":"item.started","item":{"id":"item_1","type":"command_execution","command":"/bin/zsh -lc \"printf 'hello from codex' > hello.txt && wc -c hello.txt && cat hello.txt\"","aggregated_output":"","exit_code":null,"status":"in_progress"}}
{"type":"item.completed","item":{"id":"item_1","type":"command_execution","command":"/bin/zsh -lc \"printf 'hello from codex' > hello.txt && wc -c hello.txt && cat hello.txt\"","aggregated_output":"      16 hello.txt\nhello from codex","exit_code":0,"status":"completed"}}
{"type":"item.completed","item":{"id":"item_2","type":"agent_message","text":"已创建文件 `hello.txt`，内容为：\n\n`hello from codex`"}}
{"type":"turn.completed","usage":{"input_tokens":21981,"cached_input_tokens":15616,"output_tokens":128,"reasoning_output_tokens":21}}
```

### 2. stdin 输入路径

目标：确认长 prompt 可通过 stdin 传入，避免命令行长度限制。

命令：

```bash
cat <<'EOF' | codex --ask-for-approval never exec \
  --cd /tmp/nezha-codex-spike \
  --model gpt-5.3-codex \
  --sandbox workspace-write \
  --ignore-user-config \
  --ignore-rules \
  --ephemeral \
  --json \
  --output-last-message /tmp/nezha-codex-spike/.codex-last-message.txt \
  -
Create a file named stdin-test.txt.
Content: stdin works.
EOF
```

观察：

- stdin 是否完整进入模型
- stdout JSONL 是否和 prompt argument 模式一致

已验证结果（2026-05-28，Codex CLI 0.130.0）：

- stdin 输入：成功
- cwd 写入：成功创建 `stdin-test.txt`
- stdout JSONL：结构与 prompt argument 模式一致
- `.codex-last-message.txt`：正常写入最终回复
- usage：`turn.completed.usage` 正常存在
- 结论：Codex runtime 实现应优先通过 stdin 传入 prompt，避免命令行长度限制。

示例事件：

```json
{"type":"thread.started","thread_id":"019e6d5c-edc2-7b30-9f4a-331808d4e939"}
{"type":"turn.started"}
{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"我会在当前目录创建 `stdin-test.txt`，并写入指定内容 `stdin works.`，然后确认文件已生成。"}}
{"type":"item.started","item":{"id":"item_1","type":"command_execution","command":"/bin/zsh -lc \"cat > stdin-test.txt <<'EOF'\nstdin works.\nEOF\nls -l stdin-test.txt && printf '---\\\\n' && cat stdin-test.txt\"","aggregated_output":"","exit_code":null,"status":"in_progress"}}
{"type":"item.completed","item":{"id":"item_1","type":"command_execution","command":"/bin/zsh -lc \"cat > stdin-test.txt <<'EOF'\nstdin works.\nEOF\nls -l stdin-test.txt && printf '---\\\\n' && cat stdin-test.txt\"","aggregated_output":"-rw-r--r--@ 1 ganlu  wheel  13 May 28 14:54 stdin-test.txt\n---\nstdin works.\n","exit_code":0,"status":"completed"}}
{"type":"item.completed","item":{"id":"item_2","type":"agent_message","text":"已创建文件 `stdin-test.txt`，内容为：\n\n`stdin works.`"}}
{"type":"turn.completed","usage":{"input_tokens":22016,"cached_input_tokens":19712,"output_tokens":140,"reasoning_output_tokens":18}}
```

### 3. JSONL 事件结构

目标：确认能否映射 `SessionEvent`。

命令：

```bash
codex --ask-for-approval never exec \
  --cd /tmp/nezha-codex-spike \
  --model gpt-5.3-codex \
  --sandbox workspace-write \
  --ignore-user-config \
  --ignore-rules \
  --ephemeral \
  --json \
  "List files, then tell me what you saw." \
  > /tmp/nezha-codex-spike/events.jsonl
```

观察字段：

| 观察项            | 需要记录                             |
| -------------- | -------------------------------- |
| assistant text | event type / 字段路径                |
| tool call      | event type / tool name / args 字段 |
| tool result    | event type / stdout/stderr 字段    |
| turn completed | event type / 是否含 usage           |
| final answer   | 是否只在 output-last-message 文件中     |

输出归档：

```bash
head -20 /tmp/nezha-codex-spike/events.jsonl
tail -20 /tmp/nezha-codex-spike/events.jsonl
```

已验证结果（2026-05-28，Codex CLI 0.130.0）：

- JSONL 可稳定解析。
- agent 文本通过 `item.completed` + `item.type=agent_message` 输出。
- shell 工具调用通过 `item.started` + `item.type=command_execution` 输出。
- shell 工具结果通过 `item.completed` + `item.type=command_execution` 输出。
- token usage 通过 `turn.completed.usage` 输出。
- 结论：第一版 Codex runtime 可以支持 `tool_events=True`，至少将 `command_execution` 映射为 Bash 工具事件。

示例事件：

```json
{"type":"thread.started","thread_id":"019e6d6a-5955-70e2-98b8-92a6acd11f01"}
{"type":"turn.started"}
{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"我会先列出当前工作区文件结构，再快速总结我看到的内容。"}}
{"type":"item.started","item":{"id":"item_1","type":"command_execution","command":"/bin/zsh -lc 'rg --files'","aggregated_output":"","exit_code":null,"status":"in_progress"}}
{"type":"item.completed","item":{"id":"item_1","type":"command_execution","command":"/bin/zsh -lc 'rg --files'","aggregated_output":"hello.txt\nevents.jsonl\nstdin-test.txt\n","exit_code":0,"status":"completed"}}
{"type":"item.completed","item":{"id":"item_2","type":"agent_message","text":"我看到了 3 个文件：\n\n- `hello.txt`\n- `events.jsonl`\n- `stdin-test.txt`\n\n目前目录下只有这些文件，没有子目录内容被 `rg --files` 列出。"}}
{"type":"turn.completed","usage":{"input_tokens":21929,"cached_input_tokens":18176,"output_tokens":108,"reasoning_output_tokens":0}}
```

### 4. 失败路径

目标：确认失败时 return code、stderr、JSONL、last-message 如何表现。

命令：

```bash
codex --ask-for-approval never exec \
  --cd /tmp/nezha-codex-spike \
  --model gpt-5.3-codex \
  --sandbox read-only \
  --ignore-user-config \
  --ignore-rules \
  --ephemeral \
  --json \
  --output-last-message /tmp/nezha-codex-spike/.codex-last-message.txt \
  "Write a file named should-fail.txt" \
  > /tmp/nezha-codex-spike/fail-events.jsonl \
  2> /tmp/nezha-codex-spike/fail-stderr.txt
echo $?
cat /tmp/nezha-codex-spike/fail-stderr.txt
tail -20 /tmp/nezha-codex-spike/fail-events.jsonl
```

观察：

- sandbox 拒绝时是否 return code 非 0
- JSONL 是否包含 error event
- last-message 是否仍写入
- 模型是否会自我修正或只报告失败

已验证结果（2026-05-28，Codex CLI 0.130.0）：

- return code：`0`
- stderr：空
- JSONL：没有显式 error event
- command_execution：没有出现；Codex 直接判断只读沙箱无法写入
- last-message：正常写入失败说明
- 结论：Codex CLI 的进程退出码不能直接等价于 task 成败。对于 DAG task，仍应依赖 nezha 的验证层（`passes=true` / verification command）判断任务是否完成。

示例事件：

```json
{"type":"thread.started","thread_id":"019e6d77-443e-7420-b049-dcbb8226fee6"}
{"type":"turn.started"}
{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"我会在当前目录创建 `should-fail.txt`，先直接执行一次写入命令并反馈结果。"}}
{"type":"item.completed","item":{"id":"item_1","type":"agent_message","text":"创建失败：当前环境是只读沙箱，无法写入文件。\n\n报错为：`operation not permitted: should-fail.txt`。"}}
{"type":"turn.completed","usage":{"input_tokens":21827,"cached_input_tokens":19712,"output_tokens":156,"reasoning_output_tokens":49}}
```

### 5. timeout / kill 行为

目标：确认父进程超时 kill 后不会留下不可控进程。

命令：

```bash
python3 - <<'PY'
import asyncio, os, signal

async def main():
    proc = await asyncio.create_subprocess_exec(
        "codex", "--ask-for-approval", "never", "exec",
        "--cd", "/tmp/nezha-codex-spike",
        "--model", "gpt-5.3-codex",
        "--sandbox", "workspace-write",
        "--ignore-user-config",
        "--ignore-rules",
        "--ephemeral",
        "--json",
        "Wait for a long time before answering.",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await asyncio.sleep(3)
    proc.terminate()
    stdout, stderr = await proc.communicate()
    print("returncode=", proc.returncode)
    print("stdout_tail=", stdout.decode(errors="replace")[-500:])
    print("stderr_tail=", stderr.decode(errors="replace")[-500:])

asyncio.run(main())
PY
```

观察：

- `terminate()` 是否足够
- 是否需要 kill process group
- stderr 是否有可忽略噪音

已验证结果（2026-05-28，Codex CLI 0.130.0）：

- `proc.terminate()` 后 return code：`-15`
- 含义：进程收到 SIGTERM 后退出
- stdout：空
- stderr：`Reading additional input from stdin...`
- 结论：基础 timeout cleanup 可行。实现中仍建议使用 process group，超时先 SIGTERM，再短暂等待，最后 SIGKILL，避免 Codex 子进程或 shell 命令残留。

### 6. AGENTS.md 自动加载

目标：确认 Codex CLI 是否自动读取 cwd 下的 `AGENTS.md`。

命令：

```bash
cat > /tmp/nezha-codex-spike/AGENTS.md <<'EOF'
Always answer with the prefix: FROM_AGENTS_MD
EOF

codex --ask-for-approval never exec \
  --cd /tmp/nezha-codex-spike \
  --model gpt-5.3-codex \
  --sandbox workspace-write \
  --ignore-user-config \
  --ignore-rules \
  --ephemeral \
  --json \
  --output-last-message /tmp/nezha-codex-spike/.codex-last-message.txt \
  "Say hi."

cat /tmp/nezha-codex-spike/.codex-last-message.txt
```

观察：

- 输出是否包含 `FROM_AGENTS_MD`
- 如果自动加载，后续 Codex runtime 可减少一部分 prompt 注入。
- 如果不自动加载，沿用 nezha 当前 prompt 拼接机制。

已验证结果（2026-05-28，Codex CLI 0.130.0）：

- 输出包含 `FROM_AGENTS_MD`
- `--ignore-user-config --ignore-rules --ephemeral` 不影响 cwd 下 `AGENTS.md` 自动加载
- 结论：Codex CLI 会自动读取工作目录下的 `AGENTS.md`。Codex runtime 需要避免把同一份项目规则既放在 `AGENTS.md` 又重复拼进 prompt，防止上下文膨胀和指令重复。

示例事件：

```json
{"type":"thread.started","thread_id":"019e6db8-f070-70f2-80c0-936317e8b181"}
{"type":"turn.started"}
{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"FROM_AGENTS_MD 你好！"}}
{"type":"turn.completed","usage":{"input_tokens":10916,"cached_input_tokens":6528,"output_tokens":11,"reasoning_output_tokens":0}}
```

### 7. MCP 行为

目标：确认 Codex MCP 是全局配置还是可 per-session 注入。

命令：

```bash
codex mcp --help
codex exec --help | grep -i mcp
```

观察：

- `codex exec` 是否支持临时 MCP 参数。
- 如果只支持全局配置，nezha runtime 不应把 MCP 当作 per-session 公共能力。

已验证结果（2026-05-28，Codex CLI 0.130.0）：

- `codex mcp` 支持 `list/get/add/remove/login/logout`
- `codex exec --help | grep -i mcp` 无输出
- 结论：Codex CLI 的 MCP 管理主要走 Codex 配置，不提供明显的 `exec` per-session MCP 参数。第一版 `CodexCliRuntime` 不应承诺支持 per-session MCP 注入。

## Codex → nezha 映射草案

### SessionResult 映射

| nezha 字段        | Codex 来源                                   |
| --------------- | ------------------------------------------ |
| `status`        | process return code + JSONL fatal/error event；注意 agent 自述失败不一定导致非 0 |
| `duration_ms`   | 父进程计时                                      |
| `num_turns`     | JSONL `turn.completed` event 计数，若无则 0      |
| `cost_usd`      | 第一版 `None`                                 |
| `input_tokens`  | `turn.completed.usage.input_tokens`，若无则 0  |
| `output_tokens` | `turn.completed.usage.output_tokens`，若无则 0 |
| `result_text`   | `--output-last-message` 文件                 |
| `error`         | stderr + JSONL fatal/error 摘要；普通 task 失败主要交给验证层 |

### SessionEvent 映射

基于基础成功路径，JSONL 事件至少可以这样映射：

| Codex JSONL                                          | nezha 事件                                                                     |
| ---------------------------------------------------- | ---------------------------------------------------------------------------- |
| `type=item.completed`, `item.type=agent_message`     | `thinking`，`data.text=item.text`                                             |
| `type=item.started`, `item.type=command_execution`   | `tool_call`，`tool=Bash`，`input.command=item.command`                         |
| `type=item.completed`, `item.type=command_execution` | `tool_result`，`success=item.exit_code == 0`，`content=item.aggregated_output` |
| `type=turn.completed`, `usage=...`                   | 不作为 `SessionEvent` 输出；用于汇总 `SessionResult`                                   |
| unknown event                                        | 忽略或 debug log                                                                |

如果 JSONL tool 事件不稳定，第一版 Codex runtime 可以只输出最终 `SessionResult`，EventBus 降级显示。

## 可能的第一版 CodexRuntime 设计

```python
class CodexCliRuntime:
    name = "codex_cli"
    capabilities = RuntimeCapabilities(
        tool_events=True,       # command_execution 可映射为 Bash tool_call/tool_result
        cost_tracking=False,
        token_tracking=True,    # turn.completed.usage 暴露 token
        pre_tool_hook=False,
        sandbox=True,
        mcp=False,              # spike 后再决定
        output_schema=True,
        resume=False,
    )
```

CLI 构造：

```text
codex --ask-for-approval never exec
  --cd <cwd>
  --model <model>
  --sandbox <sandbox>
  --ignore-user-config
  --ignore-rules
  --ephemeral
  --json
  --output-last-message <workspace>/.codex-last-message.txt
  -
```

prompt 通过 stdin 输入，避免命令行长度限制。

## Spike 验收结论模板

完成 spike 后，在本节填写：

```text
Codex CLI version: 0.130.0
Model: gpt-5.3-codex
Auth mode: local Codex auth

Result:
- prompt via stdin: pass
- cwd writes: pass
- output-last-message: pass
- JSONL parseable: pass
- tool events stable: partial (command_execution is stable in basic run; more cases pending)
- usage exposed: yes
- sandbox failure detectable: partial (read-only 写入失败时 return code 仍为 0，需要验证层判断 task 成败)
- timeout cleanup safe: yes (SIGTERM returns -15; implementation should still use process-group cleanup)
- AGENTS.md auto-loaded: yes
- MCP per-session configurable: no (exec 无直接 MCP 参数；走 Codex config)

Decision:
- Can implement CodexRuntime v1: yes
- Required abstraction shape: public run_session interface + capability flags / optional protocols
- Known limitations: no cost tracking; no Claude-style PreToolUseHook; no per-session MCP injection; task success must rely on DAG verifier rather than process return code alone
```

## 决策建议

只有当以下条件成立时，才进入正式实现：

1. `codex exec` 能稳定通过 stdin 接收完整 prompt。
2. `--output-last-message` 能稳定产出最终回答。
3. process return code 或 JSONL 能可靠判断失败。
4. timeout 后进程能被干净终止。
5. sandbox 设置能满足后台无人值守执行。

不要求第一版满足：

- cost 统计
- token 统计
- tool events 完整映射
- Claude 等价的 PreToolUseHook
- per-session MCP 注入

这些能力通过 `RuntimeCapabilities` 表达，后续逐步补齐。
