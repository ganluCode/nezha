# Codex 集成计划（搁置中）

> 状态：**已决策但暂不实施**
>
> 时机：等可视化（Phase C3 / 桌面端 base framework）完成后再做
>
> 决策日期：2026-04-19

## 决策摘要

1. **要做**：把 OpenAI Codex 作为 nezha 的可选 runtime（与 claude-code-sdk 并存）
2. **走 CLI 路径**：用 `codex exec` 命令行模式，**不用** Python SDK（codex_app_server）
3. **暂不实施**：等桌面端可视化做完再回头做

## 为什么走 CLI 不走 SDK

### 调研发现

| 维度 | claude-code-sdk（现状） | Codex Python SDK | Codex CLI |
|------|----------------------|-----------------|-----------|
| 稳定性 | 较稳定 | **实验性** | **稳定**（735+ release，77.5k stars） |
| 子进程隔离 | 同进程，被迫子进程包装（anyio 坑） | 同上风险 | **天然子进程** |
| API 表面 | 大 | 小 | 小但够用 |
| 成本追踪 | 暴露 | 未文档化 | 不暴露（不痛不痒） |
| MCP 支持 | 配置文件 | 配置文件 | **命令行原生** |
| Tool 控制 | PreToolUseHook | sandbox policy | sandbox policy |

### 关键洞察

nezha 的核心架构约束是「**子进程隔离**」（每个 session 独立 Python 子进程跑），这就是 Unix 哲学。

CLI 模式（`codex exec` / `claude -p`）天然契合这个架构，反而比 SDK 更适合 nezha。

> nezha 当初选 SDK 是历史原因（Pythonic + 细粒度 events + hooks），但 anyio 坑后变相退化成「subprocess 套 SDK」，等于既没 SDK 优势又没 CLI 简洁。

## 集成方案（执行时参考）

### 改动清单（预估 1-2 天）

#### 1. 新增 `src/nezha/pipeline/codex_session.py`

```python
import subprocess, json, asyncio
from pathlib import Path
from nezha.engine import SessionResult

async def run_codex_session(
    prompt: str,
    cwd: Path,
    model: str = "gpt-5.4",
    sandbox: str = "workspace-write",
    full_auto: bool = True,
    timeout: int = 1800,
) -> SessionResult:
    """运行 Codex CLI 作为 session（替代 claude-code-sdk）"""

    output_file = cwd / ".codex-result.txt"

    cmd = [
        "codex", "exec",
        "--cd", str(cwd),
        "--sandbox", sandbox,
        "--model", model,
        "--json",
        "--output-last-message", str(output_file),
    ]
    if full_auto:
        cmd.append("--full-auto")

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(prompt.encode()), timeout=timeout
        )
    except asyncio.TimeoutError:
        proc.kill()
        return SessionResult(status="error", error="timeout")

    # 解析 --json 事件流
    events = []
    for line in stdout.decode().split('\n'):
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    num_turns = sum(1 for e in events if e.get("type") == "turn.completed")
    result_text = output_file.read_text() if output_file.exists() else ""

    return SessionResult(
        status="completed" if proc.returncode == 0 else "error",
        num_turns=num_turns,
        result_text=result_text,
        # cost_usd / tokens 拿不到，留空（Codex CLI 不暴露）
    )
```

#### 2. 改 `src/nezha/pipeline/session.py` 增加 dispatcher

```python
async def run_single_round(executor_config, agent_config, ...):
    runtime = getattr(agent_config.engine, "runtime", "claude_code")
    if runtime == "codex":
        from nezha.pipeline.codex_session import run_codex_session
        return await run_codex_session(...)
    # 现有 claude-code-sdk 路径不变
    ...
```

`run_multi_round` 同理。

#### 3. 改 `src/nezha/config.py` 增加字段

```python
@dataclass
class EngineConfig:
    runtime: str = "claude_code"     # claude_code | codex（新增）
    api_type: str = "anthropic"      # anthropic | openai（已有）
    model: str = "claude-sonnet-4-6"
    # ... 其他字段不变
```

#### 4. 新增模板 `src/nezha/templates/agents/codex-evolve-agent.yaml`

```yaml
agent:
  name: "codex-evolve-agent"
  category: "coding"
  description: "用 OpenAI Codex 跑的编码 agent"

engine:
  runtime: "codex"        # 关键字段
  model: "gpt-5.4"
  # Codex CLI 自己管 max_turns，不用 nezha 配
  security:
    # Codex 用 sandbox 替代 allowed_commands
    sandbox: "workspace-write"

session:
  mode: "single_round"
  prompts:
    worker: "evolve/worker.md"   # 用现有 prompt，CLI 接收为 user prompt

git:
  branch_per_task: true
  use_worktree: true
  base_branch: "main"
  auto_commit: true
```

#### 5. 上下文注入适配

**改动点**：claude-code-sdk 用 `system_prompt` 参数，Codex CLI 用 user prompt。需要在 `codex_session.py` 里把 nezha 拼好的 system prompt + user prompt 合并成一个 prompt 传给 `codex exec`。

或者：把 nezha 的项目知识注入改成生成 `AGENTS.md`（Codex 原生格式），运行时自动加载。

#### 6. 测试

新增 `tests/test_codex_session.py`：
- mock subprocess，验证命令行参数构造正确
- mock stdout，验证事件流解析正确
- 验证 dispatcher 根据 runtime 字段正确路由

## 实施前的 Spike（半天）

执行前先验证几个不确定点：

```bash
# 1. Codex CLI 是否在 macOS / Linux / Windows 都能装
brew install codex   # 或 npm install -g @openai/codex

# 2. exec 模式 + JSON 输出能拿到什么事件
echo "create a hello world python file" | codex exec --json --full-auto --sandbox workspace-write --cd /tmp/test 2>&1 | head -50

# 3. 验证 nezha 的 worker prompt 用 codex exec 能跑通
echo "$(cat src/nezha/templates/prompts/python/worker.md)" | codex exec --json --full-auto --sandbox workspace-write --cd /path/to/test/repo

# 4. 错误处理：故意触发非法命令，看 exit code 和错误格式
```

## 决策依据回顾

### 用户的核心关注点（验证过都满足）

1. ✅ **能否按 task_list.json 执行任务** — 完全可以（同 claude-code-sdk）
2. ✅ **能否读取上下文** — 可以（Codex 有完整的 Read 工具 + AGENTS.md）
3. ✅ **MCP 支持** — 原生支持，比 claude-code-sdk 还方便（命令行管理）

### 用户认为不重要的（接受 trade-off）

- ❌ Cost / token 追踪：Codex CLI 不暴露 → 不痛不痒
- ❌ max_turns 控制：Codex 自己管 → 不需要 nezha 控制

## 时间线

```
Now:  ✅ 调研完成、方案确定、文档存档
↓
等待: 桌面端 base framework 完成（PyTauri/Tauri spike → 网络/SQLite/授权/混淆等）
等待: Phase C3 可视化能力（实时 Dashboard 等）
↓
Then: 回到这份文档，按上面 6 步实施 Codex CLI runtime 集成
↓
Done: nezha 支持双 runtime（claude_code + codex），用户在 agent YAML 自由切换
```

## 长期启发（顺便记一下）

这次讨论沉淀的工程原则：

1. **CLI > SDK 当架构核心是子进程隔离时**
   - nezha、orchestrator 类项目都适用
   - 「Unix 哲学 vs 程序内嵌」选 Unix 哲学

2. **集成新 LLM 工具优先看 CLI 是否完善**
   - CLI 通常比 SDK 稳定
   - 跨语言（Rust/TS/Python 都不影响调用方）
   - 减少传递依赖

3. **Anthropic 和 OpenAI 的工具策略对比**
   - Claude Code：SDK 优先，CLI 补充
   - Codex：CLI 优先（Rust binary），Python SDK 是 wrapper
   - OpenAI 的策略对 nezha 更友好

## 参考资料

- [Codex CLI 命令行参考](https://developers.openai.com/codex/cli/reference)
- [Non-interactive mode (codex exec)](https://developers.openai.com/codex/noninteractive)
- [Codex SDK 文档](https://developers.openai.com/codex/sdk)
- [openai/codex GitHub](https://github.com/openai/codex)
- [docs/harness-engineering.md](harness-engineering.md) — 我们之前的 harness 调研
