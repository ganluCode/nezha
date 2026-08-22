# 故障排查

按错误信息查问题。**先搜你看到的错误关键词**。

## 安装与命令

### `nezha: command not found`

pipx 路径没加到 PATH：

```bash
pipx ensurepath
source ~/.zshrc      # 或 ~/.bashrc
```

### pip 安装 `403 Forbidden`

清华源限流，换官方源：

```bash
PIP_INDEX_URL=https://pypi.org/simple pipx install --force .
```

永久切换到阿里源：

```bash
pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/
```

### `ImportError: cannot import name 'XXX' from 'claude_code_sdk'`

claude-code-sdk 版本不匹配。升级 SDK：

```bash
pipx runpip nezha install -U claude-code-sdk
```

## 认证与 Token

### `Could not resolve authentication method`

`ANTHROPIC_API_KEY` 没设。要么：

- 配 `.env` 加 `ANTHROPIC_API_KEY=sk-ant-xxx`
- 或者用 Claude Code 登录态（删除 `.env` 里的 `ANTHROPIC_API_KEY`）

详见 [04-project-init](../01-quickstart/04-project-init.md)。

### `API Error: 401 invalid authentication credentials`

可能原因：

1. **`ANTHROPIC_API_KEY` 过期** — 重新生成
2. **Claude Code OAuth 过期** — 重登：
   ```bash
   claude login
   ```
3. **环境变量泄漏到子进程** — 检查 `shell env` vs `.env` 是否冲突：
   ```bash
   echo $ANTHROPIC_API_KEY     # 看 shell 里有没有
   ```
4. **Token 没权限** — 去 https://console.anthropic.com/settings/keys 检查

Nezha 会**自动识别 401 并优雅停止**，详见 [How-To: 限流处理](rate-limit.md)。

### `429 rate limit exceeded` / `529 overloaded`

API 限流，Nezha 自动停止。等几分钟到几小时后重试。

详见 [How-To: 限流处理](rate-limit.md)。

## Agent 与 Prompt

### `Agent <name>: no worker prompt configured`

agent YAML 既没配 `session.prompts.worker` 也没配 `session.compose.worker`。两选一：

**单模板模式**：

```yaml
session:
  prompts:
    worker: "coding/worker.md"
```

**Compose 模式**：

```yaml
session:
  compose:
    worker:
      base: "coding/base.md"
      sections:
        - phases/context-acquisition
        - stacks/python
```

### `Unknown message type: rate_limit_event`

旧版本 bug，升级到最新：

```bash
cd /path/to/nezha
PIP_INDEX_URL=https://pypi.org/simple pipx install --force .
```

### Prompt 模板找不到

```
FileNotFoundError: prompts/coding/worker.md
```

可能是 `nezha init` 时 prompts 没复制完整。手动同步：

```bash
cp -r /path/to/nezha/src/nezha/templates/prompts/* ./prompts/
```

或者重新 `nezha init`。

## DAG 执行

### Task 跑完后状态还是 running，卡住了

可能是限流被错误识别或者 rate_limit_event 误触发。升级到最新版（见上文）。

如果还卡：手动改 `feature.yaml` 把 status 改回 `pending` 重跑。

### Feature 一直处于 partial 状态

部分 task 重试 3 次还失败，被 skipped 了。

```bash
nezha feature show <id>      # 看哪些 task skipped
```

手动 rework：

```bash
nezha rework coding-agent T-005 "改用其他实现方式"
```

详见 [How-To: 失败处理](failure-handling.md)。

### `passes=true` 后 task 又被反复调度

旧版本 bug（rework 标记没清）。升级修复，详见 [bug 修复记录](#历史-bug-修复)。

## Git 相关

### `git push` 失败

排查：

1. 是否配了 SSH key 或 git credential helper？
2. target 仓库的 remote 是不是正确？
   ```bash
   cd target-repo
   git remote -v
   ```
3. 分支是否存在？

### `gh: command not found`

GitHub CLI 没装。要么装：

```bash
brew install gh
gh auth login
```

要么关掉 PR 自动创建（不用 `create-pr` post_tool）。

### Gitee create-pr 失败

详见 [How-To: GitHub vs Gitee](github-vs-gitee.md)。

最常见原因：`GITEE_TOKEN` 没配或过期。

## 子进程 / SDK 噪音

### 看到一堆 `cancel scope` / `anyio` 错误

```
[session] stderr: ...cancel scope... __aexit__... anyio/_backends...
```

这是 claude-code-sdk 的退出清理噪音，**不影响功能**。升级 Nezha 到最新版会过滤掉这些日志。

### `GeneratorExit` / `Task exception was never retrieved`

同上，是 SDK 异步清理的副作用。可以忽略。

## 配置加载

### `executor.yaml` 改了不生效

排查：

1. 跑命令的 working directory 对吗？默认从 `./executor.yaml` 加载。
2. `--config` 参数指向的是新文件吗？
3. 子进程读到的是不是旧值？重启 nezha：
   ```bash
   nezha stop
   nezha run frontend-agent
   ```

### `${VAR}` 没替换

`.env` 不在 `executor.yaml` 同目录，或者变量真的不存在。检查：

```bash
cat .env | grep VAR_NAME
ls -la executor.yaml .env       # 必须在同目录
```

## 性能与卡顿

### `nezha run` 很慢

可能原因：

| 原因 | 排查 |
|------|------|
| 网络慢 | `curl -w "%{time_total}" https://api.anthropic.com` |
| 模型响应慢（Opus 比 Haiku 慢） | 检查 `model_map` 设置 |
| `interval` 太长 | 改 `scheduler.interval` 小一点 |
| `verification.command` 太慢 | 优化测试命令或加 timeout |

### Feature 跑了很久没动

可能子进程卡住了。看：

```bash
nezha logs -f
```

如果完全静默，强制停：

```bash
nezha stop --force
```

然后 `feature.yaml` 改回 `pending` 重跑。

## 心跳

### `[heartbeat] No models configured`

`executor.yaml` 没配 `heartbeat.models`。详见 [How-To: 心跳保活](heartbeat.md)。

### `[heartbeat] Already running (pid=XXX)`

幂等保护，已经启动了。要重启：

```bash
nezha heartbeat stop
nezha heartbeat start
```

## 历史 bug 修复

如果遇到下面这些症状，**先升级 Nezha 到最新版**：

| 症状 | 修复版本 | 原因 |
|------|---------|------|
| `passes=true` 后 task 反复调度 | 最新 | DAG 状态判定优先级错 |
| `rate_limit_event` 被误判限流 | 最新 | 信息性消息被当成错误 |
| Compose 模式间歇报 "no worker prompt" | 最新 | `run_single_round` 没检查 compose |
| `nezha init` 生成的 `/status` skill 覆盖 Claude Code 原生 | 最新 | skill 重命名为 `/overview` |

## 调试技巧

### 看完整的 stderr

```bash
nezha run frontend-agent 2>&1 | tee debug.log
```

### 单步调试

用 vibe 模式：

```bash
nezha vibe coding-agent --feature-id <id>
```

进入交互式 REPL，一步步控制。

### 跳过 planner 自己控制 task

```bash
nezha run frontend-agent --skip-planner
```

然后手动写 `task_list.json`。

### 检查环境

```bash
nezha status         # 看 executor 状态
nezha heartbeat test # 验证模型认证
echo $ANTHROPIC_API_KEY $GH_TOKEN | xargs -n1
```

## 还是解决不了？

1. **查 issue**：https://github.com/<your-org>/nezha/issues
2. **看代码**：`src/nezha/` 是 src layout，模块清晰
3. **看日志**：`state/logs/` 下有完整事件日志
4. **看 trace**：`state/trace.jsonl` 有链路追踪
5. **提 issue**：贴出错误信息 + executor.yaml + 操作复现步骤

## 相关章节

- [How-To: 限流处理](rate-limit.md)
- [How-To: 失败处理](failure-handling.md)
- [How-To: 心跳保活](heartbeat.md)
- [Reference: CLI 命令](../02-cheatsheet/cli-commands.md)
