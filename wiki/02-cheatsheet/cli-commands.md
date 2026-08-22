# CLI 命令速查

按使用场景分组。所有命令都接受 `--config <path>`，默认读 `./executor.yaml`。

## 项目初始化

| 命令 | 用途 |
|------|------|
| `nezha init <name>` | 创建 Harness 工程目录 |
| `nezha project init` | 二次初始化，创建 `workspace/project/` |
| `nezha agent-context init <agent>` | 给某个 agent 创建跨任务记忆文件 |

## 执行 Agent

| 命令 | 用途 |
|------|------|
| `nezha run <agent>` | 启动 Agent 执行（最常用） |
| `nezha run <agent> --feature-id <id>` | 只跑指定 Feature |
| `nezha run <agent> --max-iterations 10` | 限制最大轮次 |
| `nezha run <agent> --background` | 后台执行（写 PID 文件） |
| `nezha run <agent> --at 02:00` | 定时启动 |
| `nezha run <agent> --delay 5m` | 延迟启动 |
| `nezha run <agent> --skip-planner` | 跳过 planner，agent 自己规划 |
| `nezha run <agent> --mode gardening` | 指定执行模式 |
| `nezha vibe <agent>` | 交互式 REPL（vibe coding） |
| `nezha code <agent>` | 启动 Claude Code（预配置 agent 上下文） |

## Feature 管理

| 命令 | 用途 |
|------|------|
| `nezha feature create --title "..."` | 创建 Feature |
| `nezha feature create --title "..." --input prd.md` | 带 PRD 输入 |
| `nezha feature create --title "..." --base-branch main` | 指定基分支 |
| `nezha feature list` | 列出所有 Feature |
| `nezha feature list --status pending` | 按状态筛选（pending/running/completed/partial/failed） |
| `nezha feature show <id>` | 看 Feature 详情 + 费用 |
| `nezha feature approve <id> <step-id>` | 人工审批通过 |
| `nezha feature reject <id> <step-id> --note "..."` | 人工拒绝（带原因） |
| `nezha feature push <id>` | 推送 Feature 分支到远程 |

> `nezha task` 是 `nezha feature` 的别名（向后兼容）。

## Phase 编排

| 命令 | 用途 |
|------|------|
| `nezha phase plan phase.yaml` | 从 phase YAML 批量创建 Feature |
| `nezha phase plan phase.yaml --skip-planner` | 只建 Feature 不拆 Task |
| `nezha phase plan phase.yaml --base-branch main` | 指定 Phase 起始分支 |
| `nezha phase list` | 列出所有 Phase |
| `nezha phase show <id>` | 看 Phase 状态（ASCII DAG 树） |

## 状态查看

| 命令 | 用途 |
|------|------|
| `nezha status` | 当前执行状态 + 最近 session 费用 |
| `nezha history` | 历史执行记录 |
| `nezha logs` | 看日志 |
| `nezha logs -f` | 实时 tail 日志 |
| `nezha plan <agent>` | 显示 Task 依赖 DAG |
| `nezha dashboard` | 生成 HTML dashboard |
| `nezha dashboard --open` | 生成并打开浏览器 |
| `nezha dashboard -o report.html` | 自定义输出路径 |
| `nezha agent-context show <agent>` | 查看 agent 跨任务记忆 |

## 控制执行

| 命令 | 用途 |
|------|------|
| `nezha stop` | 优雅停止（推荐） |
| `nezha stop --force` | 立即强制停（SIGTERM） |
| `nezha pause` | 暂停执行 |
| `nezha resume` | 恢复执行 |

## 修复重做

| 命令 | 用途 |
|------|------|
| `nezha rework <agent> <task-ids> "原因"` | 标记 task 重做 |
| `nezha rework coding-agent F-003,F-005 "样式不对"` | 多任务一起标记 |

## 分支合并

| 命令 | 用途 |
|------|------|
| `nezha integrate 1 2 3` | 合并 Feature 1/2/3 到临时分支 |
| `nezha integrate 1 2 --base main` | 指定基分支 |
| `nezha integrate 1 2 --branch review/v1` | 指定合并分支名 |
| `nezha integrate 1 2 --push` | 合并后推送到远程 |
| `nezha integrate feat/f01 feat/f02 --repo /path/to/repo` | 跨目录合并 |

## 心跳保活

| 命令 | 用途 |
|------|------|
| `nezha heartbeat start` | 后台启动心跳进程 |
| `nezha heartbeat test` | 立即 ping 一次（验证配置） |
| `nezha heartbeat stop` | 停止心跳 |

## 高频组合命令

**完整工作流一行命令链**：

```bash
nezha init my-app && cd my-app
# 编辑 executor.yaml 配 target 和 model_map
nezha project init
claude  # 在 Claude Code 里 /architecture → /prd → /phase-plan
nezha run frontend-agent
```

**调试单 Feature**：

```bash
nezha feature create --title "调试某功能" --input debug.md
nezha run coding-agent --feature-id <生成的 id>
```

**只看摘要不看日志**：

```bash
nezha feature list --status running
nezha dashboard --open
```

**夜间长跑**：

```bash
nezha run coding-agent --background --at 22:00
nezha heartbeat start
```

**全局选项**

`--config <path>`：所有命令都支持，默认 `./executor.yaml`。在子目录里跑命令时用：

```bash
nezha status --config ../my-harness/executor.yaml
```
