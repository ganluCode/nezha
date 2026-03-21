# Nezha (哪吒)

> 取名"哪吒"，寓意三头六臂 —— 多 Agent 并行协作，高效自动化执行。

YAML 驱动的 AI Agent 编排执行框架，基于 [Claude Code SDK](https://docs.anthropic.com/en/docs/claude-code/sdk)。

[![Python](https://img.shields.io/badge/Python-≥3.12-blue.svg)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/Tests-933%20passed-brightgreen.svg)](#测试)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Nezha 将 AI 编码 Agent 从"对话工具"提升为**可编排、可观测、可持续运行的工程系统**。你只需编写 YAML 配置，就能驱动多个 Agent 按 DAG 依赖顺序自动完成复杂的软件工程任务。

---

## 特性

- **YAML 配置驱动** — Agent、调度器、守卫、事件处理器全部声明式配置，零代码即可编排
- **多 Agent 协作** — coding / planning / management 等多种 Agent 类型，支持 callable 互调
- **DAG 依赖调度** — task_list.json 定义任务依赖图，自动按拓扑顺序执行
- **Feature Queue** — 需求粒度的任务队列，支持状态流转、分步审批、优先级排序
- **三种 Session 模式** — single_round（单次）/ multi_round（DAG 多轮）/ direct（API 直连）
- **Git 自动化** — 按任务创建分支、worktree 隔离、自动 commit/push
- **安全守卫链** — 熔断器、余额检查、时间窗口，执行前自动拦截
- **事件系统** — 文件日志、状态跟踪、执行轨迹，全链路可观测
- **并行执行** — asyncio.Semaphore 控制并发度，多 Feature 同时执行
- **费用追踪** — 自动解析 execution-report.md，汇总 session 级别费用
- **静态 Dashboard** — 一键生成 HTML 可视化仪表盘
- **Prompt 组合器** — 模块化 Prompt 系统，base + phases + stacks + concerns 自由组合
- **model_map 模型映射** — 按任务复杂度自动选择模型，支持多厂商 API Key/Base URL 切换
- **多模型支持** — 原生支持 Claude，通过 `ANTHROPIC_BASE_URL` 兼容第三方模型
- **Claude Code 原生集成** — `init` 自动生成 CLAUDE.md + 16 个交互技能（/status, /prd, /review, /rework 等），支持 EN/ZH 国际化
- **AI Judge 多模型** — 失败策略支持 Anthropic + OpenAI 兼容 API 评判是否继续执行

---

## 架构概览

```
┌─────────────────────────────────────────────────────┐
│                     CLI (nezha)                │
├─────────────────────────────────────────────────────┤
│                                                     │
│   executor.yaml ──→ Executor                        │
│       │                │                            │
│       │          ┌─────┴─────┐                      │
│       │          │ GuardChain│ (熔断/余额/时间窗口)  │
│       │          └─────┬─────┘                      │
│       │                │                            │
│       │          ┌─────┴─────┐                      │
│       │          │ Scheduler │ (manual/continuous)   │
│       │          └─────┬─────┘                      │
│       │                │                            │
│       ▼          ┌─────┴──────────────┐             │
│   agents/*.yaml  │   Session Runner   │             │
│       │          │  ┌──────────────┐  │             │
│       │          │  │  DAG Engine  │  │             │
│       │          │  └──────┬───────┘  │             │
│       │          │         │          │             │
│       ▼          │  ┌──────┴───────┐  │             │
│   prompts/       │  │  LLM Engine  │  │             │
│                  │  │ (claude-sdk) │  │             │
│                  │  └──────────────┘  │             │
│                  └────────────────────┘             │
│                        │                            │
│                  ┌─────┴─────┐                      │
│                  │ EventBus  │ (日志/状态/轨迹)      │
│                  └───────────┘                      │
│                                                     │
│   workspace/          target/                       │
│   (元数据)             (代码仓库)                     │
└─────────────────────────────────────────────────────┘
```

---

## 快速开始

### 前置依赖

- Python ≥ 3.12
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) (`claude` 命令可用)
- Git

### 安装

```bash
# 1. 克隆仓库
git clone https://github.com/your-org/nezha.git
cd nezha

# 2. 安装 uv（如未安装）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 3. 创建虚拟环境 + 安装
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"

# 或使用 Makefile（等效）
make venv && source .venv/bin/activate
make install-dev

# 4. 全局安装（可选，nezha 命令写入 PATH）
pipx install .

# 5. 验证
nezha --help
```

### 全局用户配置（可选，一次性）

```bash
# 创建全局配置目录
mkdir -p ~/.nezha

# 设置默认偏好（init 新项目时自动应用）
cat > ~/.nezha/config.yaml << 'EOF'
locale: "zh_CN"
timezone: "Asia/Shanghai"
model_map:
  low: "claude-sonnet-4-6"
  medium: "claude-sonnet-4-6"
  high: "claude-opus-4-6"
EOF
```

### 初始化项目

```bash
# 1. 创建 executor 工作空间（与代码仓库分离）
nezha init /path/to/my-project
cd /path/to/my-project

# 2. 配置敏感变量 — 复制 .env.example 并填入实际值
cp .env.example .env
# 编辑 .env，填入 ANTHROPIC_API_KEY、GH_TOKEN 等

# 3. 配置目标代码仓库 — 编辑 executor.yaml
#    修改: target: "/path/to/your/code-repo"

# 4. 初始化项目知识库（可选，生成 PRD 模板等）
nezha project init

# 5. 使用 Claude Code 交互式协作（可选）
#    init 已自动生成 CLAUDE.md + .claude/skills/
#    在项目目录中直接运行 claude，输入 / 查看所有技能
claude
```

初始化后的目录结构：

```
my-project-executor/
├── executor.yaml          # 全局配置（调度、守卫、事件处理器）
├── CLAUDE.md              # Claude Code 项目说明（自动生成，@导入配置文件）
├── agents/                # Agent 配置
│   ├── coding-agent.yaml
│   ├── planner-agent.yaml
│   ├── helper-agent.yaml
│   └── ...
├── prompts/               # 自定义 Prompt（覆盖内置模板）
├── workspace/             # Agent 运行时工作空间
│   └── project/           # 项目知识库（nezha project init 生成）
├── input/                 # 任务输入文件（需求文档等）
├── .claude/               # Claude Code 配置（自动生成）
│   ├── settings.json      #   权限规则
│   └── skills/            #   16 个交互技能（EN/ZH 国际化）
└── .gitignore
```

### 第一次运行

```bash
# 最简单的方式：直接给 Agent 一个任务
nezha run coding-agent --prompt "添加一个健康检查 API endpoint /health"

# 或者用交互式 VibeCoding 模式
nezha vibe coding-agent
```

---

## 使用指南

### 核心概念

| 概念 | 说明 |
|------|------|
| **Executor** | 全局编排器，管理调度、守卫、事件系统 |
| **Agent** | 一个 YAML 配置的 AI 执行单元，有自己的模型、Prompt、工具集 |
| **Feature** | 一个需求/交付物，存储在 `workspace/features/<id>/` |
| **Task** | DAG 中的一个编码任务（task_list.json 条目） |
| **Session** | 一次 LLM 调用（子进程隔离运行） |
| **Guard** | 执行前的安全检查（熔断器、余额、时间窗口） |

### 1. Feature Queue 工作流（推荐）

```bash
# 创建 Feature
nezha feature create --title "用户登录"
nezha feature create --title "用户注册" --input input/spec.md

# 查看队列
nezha feature list

# 执行（自动拿 pending 状态的 Feature）
nezha run coding-agent

# 查看 Feature 详情
nezha feature show <feature-id>

# 查看仪表盘
nezha dashboard --open
```

Feature 状态流转：

```
pending → running → completed
                  → partial    (DAG 部分完成)
                  → failed     (执行出错)
```

### 2. 自动规划 + DAG 执行

```bash
# 把需求文档放到 input/ 目录
cp requirements.md input/spec.md

# 创建 Feature（关联输入文件）
nezha feature create --title "API v2" --input input/spec.md

# 运行 coding-agent — 自动调用 planner-agent 生成 task_list.json
# 然后按 DAG 依赖顺序逐个完成任务
nezha run coding-agent
```

task_list.json 示例：

```json
[
  {
    "id": "F-001",
    "description": "数据库 Schema — 创建用户表和会话表",
    "acceptance": ["迁移脚本执行成功", "表结构包含必要字段"],
    "depends_on": [],
    "complexity": "low",
    "passes": false
  },
  {
    "id": "F-002",
    "description": "用户注册 API — POST /api/register",
    "acceptance": ["合法请求返回 201", "重复邮箱返回 409"],
    "depends_on": ["F-001"],
    "complexity": "medium",
    "passes": false
  }
]
```

### 3. 分步审批（Feature Steps）

对关键 Feature 可以设置分步执行，每步完成后暂停等待人工审批：

```bash
# 查看 Feature 的 step 状态
nezha feature show <feature-id>

# 审批通过 — Agent 继续执行下一步
nezha feature approve <feature-id> <step-id>

# 打回 — Agent 重做该步骤
nezha feature reject <feature-id> <step-id> --note "缺少错误处理"
```

### 4. VibeCoding（交互式）

像聊天一样引导 Agent 写代码，适合探索性开发：

```bash
nezha vibe coding-agent

# 进入 REPL 后：
> 先看看项目结构
> 添加一个 Redis 缓存层
> 写个测试验证一下
> exit
```

### 5. Helper Agent（万能助手）

一站式控制面板，分析 + 操作：

```bash
# 分析类
nezha run helper-agent --prompt "分析当前项目架构，给出改进建议"
nezha run helper-agent --prompt "这个 PR 有什么问题"

# 操作类
nezha run helper-agent --prompt "创建一个 Feature: 实现搜索功能"
nezha run helper-agent --prompt "查看所有 Feature 状态并汇总报告"
```

---

## 配置参考

### executor.yaml

```yaml
executor:
  name: "my-project"
  description: "项目描述"

workspace:
  base: "./workspace"
  strategy: "per_agent"        # per_agent | shared

scheduler:
  mode: "manual"               # manual | continuous | cron
  # continuous 模式:
  # interval: 60               # 两轮间隔（秒）
  # concurrency: 3             # 并行执行 Feature 数量
  # cron 模式:
  # cron: "0 2 * * *"

guards:
  - type: "circuit_breaker"
    enabled: true
    max_consecutive_errors: 3
    cooldown_seconds: 600

  - type: "balance_check"
    enabled: false
    min_balance_usd: 5.0
    max_cost_usd: 50.0         # 总费用上限

  - type: "time_window"
    enabled: false
    allow: "00:00-08:00"
    timezone: "Asia/Shanghai"

event_handlers:
  - type: "file_logger"
    enabled: true
    path: "./state/logs/"
  - type: "state_writer"
    enabled: true
    path: "./state/executor_status.json"

agents_dir: "./agents"
prompts_dir: "./prompts"
state_dir: "./state"

# 目标代码仓库（所有 coding agent 共享，agent YAML 可覆盖）
target: "/path/to/your/code-repo"

env: {}
mcp_servers: {}
```

### Agent YAML

```yaml
agent:
  name: "coding-agent"
  category: "coding"           # coding | planning | design | management
  callable: false              # true = 可被其他 Agent 调用
  description: "通用编码 Agent"

engine:
  model: "claude-sonnet-4-6"         # 默认兜底模型
  max_turns: 100
  tools: [Read, Write, Edit, Bash, Glob, Grep]
  # 按复杂度自动选择模型（Planner 只输出 complexity，运维侧配置策略）:
  model_map:
    low: "claude-haiku-4-5-20251001"   # string 简写
    medium:                             # dict 完整格式
      model: "claude-sonnet-4-6"
    high:
      model: "claude-sonnet-4-6"
      env:                              # 可选：不同厂商的 key
        ANTHROPIC_API_KEY: "sk-special"
  # 第三方模型配置：
  # env:
  #   ANTHROPIC_BASE_URL: "https://your-proxy/v1"
  #   ANTHROPIC_API_KEY: "your-key"
  security:
    allowed_commands: [ls, cat, git, python3, pytest, npm]

session:
  mode: "single_round"        # single_round | multi_round | direct
  prompts:
    worker: "coding/worker.md"
  # 或使用 Prompt 组合器:
  # compose:
  #   worker:
  #     base: "coding/base.md"
  #     sections:
  #       - "phases/context-acquisition"
  #       - "phases/tdd"
  #       - "stacks/python"

# target 在 executor.yaml 中统一配置，如需覆盖可取消注释：
# target: "/path/to/override"
# target_scope: "backend"      # monorepo 子目录

git:
  branch_per_task: true
  use_worktree: true
  base_branch: "main"
  auto_commit: true
  auto_push: false

pipeline:
  # 自动调用 planner:
  # pre_agents:
  #   - name: "planner-agent"
  #     artifact: "task_list.json"
  # 集成测试:
  # post_task_test:
  #   enabled: true
  #   command: "pytest"
  #   max_cycles: 3
```

---

## 内置 Agent

| Agent | 类型 | callable | 用途 |
|-------|------|----------|------|
| coding-agent | coding | - | 通用编码，单次执行 |
| frontend-agent | coding | - | 前端 UI 开发，多轮迭代 |
| java-agent | coding | - | Java/Spring 项目 |
| planner-agent | planning | ✓ | 需求文档 → task_list.json |
| product-agent | planning | - | 需求分析 → PRD |
| business-analyst-agent | planning | - | 业务分析 |
| pm-agent | management | - | 项目管理（初始化项目、审查进度） |
| helper-agent | management | ✓ | 万能助手（9 场景：分析 + 操作） |

---

## CLI 命令速查

```bash
# 初始化
nezha init <dir>              # 创建 executor 工作空间
nezha project init            # 初始化项目知识库

# 运行
nezha run <agent>             # 执行 Agent
nezha run <agent> --prompt "..." # 带指令执行
nezha run <agent> --feature-id <id> # 指定 Feature
nezha vibe <agent>            # 交互式 VibeCoding

# Feature 管理
nezha feature create --title "..." --input ...
nezha feature list            # 列出所有 Feature
nezha feature list --status partial
nezha feature show <id>       # 查看详情（含费用）
nezha feature approve <id> <step-id>
nezha feature reject <id> <step-id> --note "..."

# 监控
nezha status                  # 查看执行状态
nezha logs -f                 # 实时日志
nezha dashboard               # 生成 HTML 仪表盘
nezha dashboard --open        # 生成并打开浏览器

# 其他
nezha plan <agent>            # 查看执行计划
nezha integrate <id1> <id2> --repo /path --branch review
nezha rework <id> --note "..."
```

---

## 目录结构

```
nezha/
├── src/nezha/            # 核心包
│   ├── __main__.py                # CLI 入口
│   ├── config.py                  # 配置解析（YAML → dataclass）
│   ├── executor.py                # 主执行器
│   ├── engine.py                  # LLM 引擎（claude-code-sdk 封装）
│   ├── feature_queue.py           # Feature Queue（Port/Adapter 模式）
│   ├── dag/                       # DAG 引擎
│   │   ├── graph.py               #   依赖图 + 状态动态计算
│   │   ├── engine.py              #   执行循环
│   │   ├── verifier.py            #   两级验证（Agent 自报告 + 外部命令）
│   │   └── report.py              #   执行报告生成
│   ├── pipeline/                  # 会话管线
│   │   ├── session.py             #   会话管理（子进程隔离）
│   │   ├── prompt_composer.py     #   Prompt 组合器
│   │   ├── knowledge.py           #   知识注入
│   │   └── security.py            #   命令白名单
│   ├── scheduler/                 # 调度策略
│   ├── guards/                    # 安全守卫
│   ├── events/                    # 事件系统
│   ├── interface/                 # 用户界面
│   │   ├── cli.py                 #   CLI 命令实现
│   │   └── dashboard.py           #   Dashboard 生成
│   └── templates/                 # 内置模板
│       ├── executor.yaml
│       ├── agents/                #   Agent YAML 模板
│       └── prompts/               #   Prompt 模板
├── agents/                        # 当前项目的 Agent 配置
├── prompts/                       # 当前项目的自定义 Prompt
├── workspace/                     # 运行时工作空间
├── tests/                         # 测试用例（933 个）
├── docs/                          # 架构设计文档
├── executor.yaml                  # 当前项目的全局配置
└── pyproject.toml
```

---

## 设计原则

### workspace / target 分离

executor 工作空间（配置、状态、元数据）与目标代码仓库完全分离：

- **workspace** — 存放 feature.yaml、task_list.json、执行报告等元数据
- **target** — Agent 操作的代码仓库（LLM 的 cwd，git 操作发生在此）

这使得一个 executor 可以服务多个代码仓库，也避免了元数据文件污染代码仓库。

### 子进程隔离

每个 Session 在独立子进程中运行，结果通过 JSON 文件传递。这解决了 claude-code-sdk 在同进程连续调用时的 event loop 污染问题，是 multi_round 模式正常工作的关键。

### Port/Adapter 模式

核心抽象均定义为 Protocol/ABC：

- `FeatureQueue` — 当前实现 `FileFeatureQueue`（文件系统），未来可替换为 Redis/MQ
- `BaseGuard` — 可扩展的守卫类型
- `BaseScheduler` — 可插拔的调度策略
- `EventHandler` — 可扩展的事件处理器

### DAG 状态动态计算

Task 的状态（ready / blocked / completed / rework / skipped）不持久化存储，每次通过 `get_status()` 根据依赖关系动态计算，避免状态不一致。

---

## 高级功能

### 并行执行

```yaml
# executor.yaml
scheduler:
  mode: "continuous"
  concurrency: 3               # 最多同时执行 3 个 Feature
```

并行模式下的安全保障：
- 每个 Feature 写入独立的 `executor_status_{feature_id}.json`
- 日志按 Feature 分文件：`{agent}_{feature_id}_{timestamp}.log`
- GuardChain 使用 `asyncio.Lock` 防止并发竞态
- 费用追踪器跨 Feature 聚合，支持 `max_cost_usd` 全局预算

### 第三方模型

通过环境变量兼容任何 Anthropic API 兼容的代理：

```yaml
# agents/coding-agent.yaml
engine:
  model: "glm-5"
  env:
    ANTHROPIC_BASE_URL: "https://open.bigmodel.cn/api/anthropic"
    ANTHROPIC_API_KEY: "your-key"
```

### model_map — 按复杂度映射模型

Planner Agent 按任务难度标注 `complexity`（low/medium/high），coding Agent 通过 `model_map` 自动选择对应模型。运维侧随时可换策略，无需修改 task_list.json。

**三层解析优先级**：`task.model`（显式指定）> `model_map[complexity]` > `engine.model`（默认）

```yaml
# agents/coding-agent.yaml
engine:
  model: "claude-sonnet-4-6"        # 默认兜底
  model_map:
    low: "claude-haiku-4-5-20251001"  # 简单任务用 Haiku（省钱）
    medium:
      model: "claude-sonnet-4-6"
    high:
      model: "claude-sonnet-4-6"
      env:                             # 可选：高复杂度用不同的 API
        ANTHROPIC_API_KEY: "sk-premium"
```

**常见策略**：

```yaml
# 省钱模式 — 全部用 Haiku
model_map:
  low: "claude-haiku-4-5-20251001"
  medium: "claude-haiku-4-5-20251001"
  high: "claude-haiku-4-5-20251001"

# 赶进度模式 — 全部用 Sonnet
model_map: {}   # 空 = 全部走 engine.model 默认

# 混合厂商模式
model_map:
  low:
    model: "deepseek-v3"
    env:
      ANTHROPIC_BASE_URL: "https://other-provider/v1"
      ANTHROPIC_API_KEY: "sk-other"
  medium:
    model: "claude-sonnet-4-6"
  high:
    model: "claude-sonnet-4-6"
```

### Prompt 组合器

模块化组合 Prompt，避免大量重复：

```yaml
session:
  compose:
    worker:
      base: "coding/base.md"          # 角色声明
      sections:
        - "phases/context-acquisition" # 上下文获取阶段
        - "phases/tdd"                 # TDD 工作流
        - "phases/commit-rules"        # 提交规范
        - "stacks/python"             # Python 技术栈
        - "concerns/quality-tracking"  # 质量跟踪
```

模块分三类：
- **phases/** — 工作流阶段（context-acquisition, rework, tdd, regression, commit-rules）
- **stacks/** — 技术栈知识（java-spring, python, frontend, general）
- **concerns/** — 横切关注点（exec-plan, quality-tracking）

### MCP Server 集成

```yaml
# executor.yaml（全局）或 agent YAML（per-agent）
mcp_servers:
  filesystem:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"]
  puppeteer:
    command: "npx"
    args: ["puppeteer-mcp-server"]
  my-remote:
    url: "http://localhost:8080/sse"
```

### 集成测试自动修复

```yaml
# agents/coding-agent.yaml
pipeline:
  post_task_test:
    enabled: true
    command: "pytest"
    max_cycles: 3              # 最多自动修复 3 轮
    timeout: 600
```

DAG 所有任务完成后自动运行集成测试，失败则启动修复 Session，循环直到通过或达到上限。

---

## 测试

```bash
# 运行全部测试
make test

# 快速检查
make test-quick

# 运行单个模块
make test-file F=test_parallel_safety.py

# 或直接使用 pytest
python -m pytest tests/ -v
```

当前 **933** 个测试，覆盖核心执行流程、DAG 引擎、Feature Queue、守卫系统、事件系统、Prompt 组合器、并行安全、模型映射等模块。

---

## 典型工作流

```
                 ┌────────────┐
                 │  用户提需求  │
                 └─────┬──────┘
                       │
          nezha feature create --title "..." --input spec.md
                       │
                       ▼
              ┌────────────────┐
              │ planner-agent  │  自动生成 task_list.json
              │  (auto invoke) │
              └───────┬────────┘
                      │
                      ▼
              ┌────────────────┐
              │ coding-agent   │  按 DAG 依赖顺序执行
              │  (multi_round) │
              └───────┬────────┘
                      │
               ┌──────┴──────┐
               │ 集成测试通过？ │
               └──────┬──────┘
                 Yes   │   No → 自动修复 Session
                      ▼
              ┌────────────────┐
              │  auto commit   │
              │  auto push     │
              └───────┬────────┘
                      │
          nezha feature list / dashboard
                      │
                      ▼
              ┌────────────────┐
              │   人工 Review   │
              └────────────────┘
```

---

## 路线图

### V2.0 — 核心能力增强 ✅

- [x] **F1** 链式分支 — `--base-branch` 支持分支链式创建
- [x] **F2** per-Task model — task_list.json 中 `"model"` 字段覆盖
- [x] **F3** 集成验证 — DAGEngine integration_prompt_path + 集成 Session
- [x] **F4** Feature Steps — 分步执行 + approve/reject 审批
- [x] **F5** 费用统计 — execution-report.md 解析 + 费用摘要

### V2.1 — 操作体验优化 ✅

- [x] **F1** Helper 全能化 — 9 场景统一控制面板（分析 + 操作）
- [x] **F2** Dashboard — 静态 HTML 可视化仪表盘
- [x] **F3** 并行执行 — asyncio.Semaphore 并发控制 + 安全隔离

### V2.2 — 多模型策略 ✅

- [x] **F1** model_map — complexity → model + env 三层解析，Planner 与模型选择解耦
- [x] **F2** 全局用户配置 — `~/.nezha/config.yaml` 自动 merge locale/timezone/env/model_map
- [x] **F3** task_factor — ModelMapEntry.task_factor + planner 粒度适配（弱模型拆细、强模型拆粗）

### V2.3 — Claude Code 原生集成 ✅

- [x] **F1** Claude Code 集成 — `nezha init` 自动生成 CLAUDE.md + `.claude/skills/` + `settings.json`，16 个交互技能（EN/ZH 国际化）
- [x] **F2** AI Judge 多模型 — `failure_strategy: "ai_judge"` 支持 Anthropic + OpenAI 兼容 API（GLM/Kimi/MiniMax）
- [x] **F3** slugify ASCII-only — Feature ID / branch 只含 ASCII 字符，中文标题保留在 title 字段供显示

### 未来方向

- [ ] Web UI — 替代 CLI 的图形化管理界面
- [ ] 多机分布式 — Redis/MQ 后端，跨机器 Agent 调度
- [ ] Webhook 事件 — 执行事件推送到外部系统
- [ ] Agent 市场 — 可共享的 Agent 配置模板

---

## 贡献

欢迎提交 Issue 和 Pull Request。

1. Fork 本仓库
2. 创建特性分支 (`git checkout -b feature/amazing-feature`)
3. 提交更改 (`git commit -m 'Add amazing feature'`)
4. 推送分支 (`git push origin feature/amazing-feature`)
5. 创建 Pull Request

请确保所有测试通过：

```bash
make test-quick
```

---

## 许可证

本项目采用 MIT 许可证 — 详见 [LICENSE](LICENSE) 文件。
