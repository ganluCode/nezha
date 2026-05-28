# Agent Executor — 项目知识库

agent-executor 是一个基于 Codex SDK 的 AI Agent 编排执行框架。核心能力：YAML 配置驱动多 Agent 协作、DAG 依赖调度、任务队列隔离、多 session 模式。

## 术语约定

- **Feature**：大需求/交付物（对应 PM 语义），存储在 `workspace/features/<id>/feature.yaml`
- **Task**：小编码任务（task_list.json 中的条目），由 DAG 引擎调度执行
- 旧代码中 `task_queue.py` / `FileTaskQueue` 是兼容层，新代码统一用 `feature_queue.py` / `FileFeatureQueue`

## 目录结构

```
src/nezha/               # Python 包（src layout）
├── __main__.py          CLI 入口（nezha 命令，所有子命令在此注册）
├── config.py            配置 dataclass（AgentConfig、ExecutorConfig、ComposeConfig 等，YAML → dataclass）
├── executor.py          主执行器（execute_agent()，串联 FeatureQueue → Guard → Scheduler → Session → Events）
├── feature_queue.py     Feature/FeatureStatus/FeatureQueue Protocol/FileFeatureQueue（Port/Adapter 模式）
├── task_queue.py         向后兼容层（TaskStatus/FileTaskQueue → 委托 feature_queue.py）
├── engine.py            LLM 引擎（Codex-sdk 封装，异步 query()）
├── dag/
│   ├── graph.py         TaskDAG（Task 依赖图数据结构，状态动态计算）
│   ├── engine.py        DAG 执行循环（pick target → run session → verify → repeat）
│   ├── verifier.py      两级验证：Agent 自报告(passes=true) + 外部命令(exit 0)
│   ├── report.py        执行报告生成（execution-report.md + exec-plan.md）
│   └── handoff.py       VibeCoding 上下文生成
├── pipeline/
│   ├── session.py       会话管理（run_single_round / run_multi_round / run_vibe_session）
│   ├── io.py            文件 I/O（input 扫描、output 目录）
│   ├── prompt_template.py  模板渲染（{{变量}} 替换）
│   ├── prompt_composer.py  Prompt 组合器（compose_prompt()，可插拔模块组合）
│   ├── knowledge.py     知识注入（load_knowledge: AGENTS.md；load_project_context: workspace/project/）
│   └── security.py      命令白名单钩子（PreToolUseHook）
├── scheduler/           调度策略（manual / continuous / cron）
├── guards/              安全守卫（CircuitBreaker / TimeWindow / BalanceCheck）
├── events/              事件系统（EventBus + FileLogger / StateWriter / TraceWriter）
└── interface/
    ├── cli.py           CLI 命令实现（run/feature/project/vibe/plan/status/history/logs/rework/integrate/dashboard）
    └── dashboard.py     静态 HTML Dashboard 生成（feature 状态 + 费用可视化）
```

关键配置文件：
```
executor.yaml            全局配置（workspace.base、scheduler、guards、event_handlers）
agents/<name>.yaml       per-Agent 配置（category、engine、session、target、git、workspace）
prompts/<name>/worker.md Worker prompt 模板（{{变量}} 注入）
prompts/modules/         可插拔 Prompt 模块目录（phases/、stacks/、concerns/）
workspace/project/       项目级共享知识（所有 Agent 共享，PM Agent 维护）
```

**Codex 集成**（`nezha init` 生成）
```
AGENTS.md                项目说明（@executor.yaml、@agents/*.yaml 导入）
.Codex/settings.json    权限配置（Bash allow: nezha *、cat *、ls *、grep *）
.Codex/skills/          16 个技能（status/feature-list/prd/review/rework/batch-features 等）
```
技能文件中的 `!`cmd`` 动态注入**必须是单条命令**，不能用 `||`、`for` 循环、管道链等复合 shell 语句（Codex 权限检查器会逐条解析拒绝）。

## 关键设计决策

**子进程隔离**（`pipeline/session.py`）
Codex-sdk 的 anyio cancel scope 在同进程内连续调用时会污染 event loop。解决方案：每个 session 在独立 Python 子进程中运行（`subprocess.run([sys.executable, "-c", script])`），结果通过 `.session_result.json` 传递。这是 multi_round 能正常工作的关键。不要在同进程内连续调用 `sdk.query()`。

**workspace / target 分离**（`executor.py`、`pipeline/session.py`）
- `workspace`：元数据目录（feature.yaml、input/、task_list.json），per-agent 隔离
- `target`：代码仓库（LLM 的 cwd，git 操作发生在此）
- coding agent（category=coding）：有 target，cwd = target
- planning/design/management agent：无 target，cwd = feature_workspace

**Port/Adapter 模式**（`feature_queue.py`）
`FeatureQueue` 是 Protocol，`FileFeatureQueue` 是当前实现。未来可以换 Redis/MQ 实现，上层代码不变。`BaseTool`、`BaseScheduler`、`BaseGuard`、`EventHandler` 同理。

**DAG 状态动态计算**（`dag/graph.py`）
Task 状态（ready/blocked/completed/rework/skipped）不存储在 Task 上，每次调用 `get_status()` 动态计算。这避免了状态不一致问题。

**project context 优先级**（`pipeline/knowledge.py`）
`load_project_context(project_dir)` 读 `workspace/project/` 下的所有文件，注入顺序先于 target 目录下的 AGENTS.md。project 层约束始终优先于 target 层。

**category 字段**（`config.py`）
`agent.category` 决定运行时行为：
- `coding`：有 target，运行安全检查，git 操作在 target
- `planning` / `design`：无 target，cwd = feature_workspace，无 git 操作
- `management`：同 planning/design，操作 workspace/project/ 目录

**model_map 三层模型解析**（`config.py` + `dag/engine.py`）
`EngineConfig.model_map` 将 task complexity（low/medium/high）映射到具体的 model + env。
解析优先级：`task.model`（task_list.json 写死）> `model_map[task.complexity]` > `engine.model`（默认兜底）。
- Planner 只输出 `complexity` 字段，不关心具体模型
- 运维侧在 agent YAML 中配置 `model_map`，随时可换策略
- `ModelMapEntry` 支持 `env` 字段，可为不同复杂度配置不同厂商的 API Key/Base URL
- 向后兼容：task_list.json 中仍可保留 `model` 字段（最高优先级）
- YAML 支持两种写法：dict 格式 `{model: "...", env: {...}}` 和 string 简写 `"model-id"`
- `_make_dataclass()` 不支持嵌套 dataclass，model_map 在 `load_agent_config()` 中手动解析
- `ModelMapEntry.task_factor`：控制 planner 任务拆分粒度，>1.0=拆细（弱模型），<1.0=拆粗（强模型）
- 默认值：low=1.2, medium=1.0, high=0.8（`_DEFAULT_TASK_FACTORS`）
- `build_model_map_info()` 格式化 model_map 信息，注入 planner prompt 的 `{{model_map_info}}` 变量
- `ExecutorConfig.model_map` 从 executor.yaml 解析（全局 config merge 过来），供 planner 读取

**FeatureStatus 六态**（`feature_queue.py`）
`pending → running → completed | partial | failed`，以及 `paused`。
- `partial`：DAG 未全部完成（deadlocked/stuck/limit），error 字段记录详情
- executor.py 通过 `dag_result.exit_reason` 判定：`all_done` → completed，其他 → partial

**PromptComposer 组合系统**（`pipeline/prompt_composer.py`）
prompt = base（角色声明）+ sections[]（可插拔模块）。模块分三类：
- `phases/`：工作流阶段（context-acquisition, rework, tdd, regression, commit-rules）
- `stacks/`：技术栈知识（java-spring, python, frontend, general）
- `concerns/`：横切关注点（exec-plan, quality-tracking）

Agent YAML 中 `session.compose.worker` 配置启用组合，无此配置时走原有 `session.prompts.worker` 路径（向后兼容）。

**Feature 目录兼容**（`feature_queue.py` + `interface/cli.py`）
`FileFeatureQueue` 优先使用有内容的 `features/` 目录，fallback 到 `tasks/`（旧版目录名）。CLI `feature list` 会同时扫描全局和 per-agent 子目录。

## Agent 一览

| Agent | category | callable | session mode | 用途 |
|-------|----------|----------|-------------|------|
| evolve-agent | coding | false | multi_round | 自我演进（target="./"） |
| planner-agent | planning | **true** | single_round | 需求 → task_list.json |
| pm-agent | management | false | single_round | 项目管理（project/ 目录） |
| frontend-agent | coding | false | multi_round | 前端 UI 开发 |
| product-agent | planning | false | single_round | 需求 → PRD |
| db-design-agent | design | false | single_round | 架构 → DDL |
| helper-agent | management | **true** | single_round | 统一控制面板（9 场景：分析 + 操作） |

## 本项目开发工具（vibecoding）

本项目自身的 Codex 开发工具在 `.Codex/` 下：

```
.Codex/
├── settings.json          # 权限：pytest / nezha / uv / git
├── skills/
│   ├── test/              # /test — 全量测试（933 个）
│   └── test-file/         # /test-file <filename> — 单文件测试
└── agents/
    └── nezha-dev.md       # @nezha-dev — 了解架构的编码 sub-agent
```

**工作方式**：直接与 Codex 对话（vibecoding），复杂编码任务用 `@nezha-dev` 委托，改完用 `/test` 验证。不走 nezha 自身的 feature → planner → evolve-agent 流程（V3 可视化完成后再切换）。

## 常用命令

```bash
# 测试
python3 -m pytest tests/ -v
python3 -m pytest tests/test_feature_queue.py -v   # 单个模块
make test                                           # 等价简写

# 安装
uv pip install -e ".[dev]"      # 开发模式（项目内 .venv）
pipx install --force .          # 全局安装（nezha 命令写入 PATH）

# 运行 Agent
nezha run evolve-agent
nezha run planner-agent --feature-id 2026-02-19-11-18-53

# 任务管理
nezha feature create --title "User Auth" --input input/spec.md
nezha feature list
nezha feature list --status partial
nezha feature show <feature-id>
nezha feature approve <feature-id> <step-id>
nezha feature reject <feature-id> <step-id> --note "reason"

# Dashboard
nezha dashboard                    # 生成 state/dashboard.html
nezha dashboard --open             # 生成并打开浏览器

# 合并分支
nezha integrate 1 2 --repo /path/to/repo --branch temp/review

# 项目知识库初始化
nezha project init

# 查看状态
nezha status
nezha logs -f
nezha plan evolve-agent
```

## 典型工作流

```
用户提需求
    ↓
nezha feature create --title "API v2" --input input/spec.md
nezha run planner-agent                          # 生成 task_list.json
    ↓
nezha feature create --input workspace/planner-agent/features/<id>/task_list.json
nezha run evolve-agent                           # DAG 驱动执行
    ↓
nezha feature list                               # 检查状态（completed/partial/failed）
python -m pytest tests/                               # 验证
```

## V2 演进路线

| Version | Feature | 状态 | 内容 |
|---------|---------|------|------|
| V2.0 | F1: 链式分支 | **已完成** | `--base-branch` 支持分支链式创建 |
| V2.0 | F2: per-Task model | **已完成** | task_list.json 中 `"model"` 字段覆盖 |
| V2.0 | F3: 集成验证 | **已完成** | DAGEngine integration_prompt_path + 集成 session |
| V2.0 | F4: Feature Steps | **已完成** | FeatureStep 数据结构 + approve/reject CLI + DAG 依赖 |
| V2.0 | F5: 费用统计 | **已完成** | `feature show` 费用摘要 + `feature list` COST 列 |
| V2.1 | F1: Helper 全能化 | **已完成** | 5→9 场景（分析 + 操作），callable=true |
| V2.1 | F2: Dashboard | **已完成** | `nezha dashboard` 生成静态 HTML 可视化 |
| V2.1 | F3: 并行执行 | **已完成** | `scheduler.concurrency` + asyncio.Semaphore 并发控制 |
| V2.2 | F1: model_map | **已完成** | complexity → model + env 三层解析，Planner 与模型选择解耦 |
| V2.2 | F2: 全局用户配置 | **已完成** | `~/.nezha/config.yaml` 自动 merge locale/timezone/env/model_map |
| V2.2 | F3: task_factor | **已完成** | ModelMapEntry.task_factor + planner 粒度适配（弱模型自动拆细） |
| V2.3 | F1: Codex 集成 | **已完成** | `nezha init` 生成 AGENTS.md + `.Codex/skills/` + `settings.json`，16 个技能（EN/ZH） |
| V2.3 | F2: AI Judge 多模型 | **已完成** | `judge_model` / `judge_api_type` / `judge_env` 支持 Anthropic + OpenAI 兼容 API |
| V2.3 | F3: slugify ASCII-only | **已完成** | Feature ID / branch 只含 ASCII，中文标题保留在 title 字段供显示 |

## 注意事项

- 修改 `src/nezha/` 下的代码后必须跑 `pytest tests/` 确认无回归（当前 933 测试）
- 新增功能需同步更新 `tests/`，evolve-agent 执行结果以测试全通过为验收标准
- `executor.yaml` 的 `workspace.base` 决定所有 agent workspace 的根目录（默认 `./workspace`）
- feature 状态卡在 `running` 时（Ctrl+C 中断），手动编辑 `feature.yaml` 将 status 改回 `pending`
- agent YAML 中的相对路径（target、workspace.path）相对于 `executor.yaml` 所在目录解析
- `_SUBPROCESS_RUNNER`（session.py）是 Python `.format()` 模板：`{var}` = 占位符，`{{`/`}}` = 转义大括号
- `_make_dataclass()` 不支持嵌套 dataclass，ComposeConfig 需手动解析（见 `load_agent_config()`）
- 全局用户配置 `~/.nezha/config.yaml`：locale/timezone/env/model_map 自动 merge 到 executor.yaml
- `resolve_prompt_path()` 支持 locale 感知：zh_CN → 优先找 `worker.zh.md`，回退 `worker.md`
- prompt 模板修改后需同步 `src/nezha/templates/prompts/` 目录（有测试检查一致性）
- `_slugify()` 只保留 ASCII `[a-z0-9_\s-]`，中文/特殊字符被过滤；Feature title 保留原始文本供显示
- AI Judge 支持 `judge_api_type: "anthropic" | "openai"`，OpenAI 兼容 API（GLM/Kimi/MiniMax）通过 `judge_env` 配置 base_url/api_key
- `.env` 文件支持（python-dotenv）：敏感配置放 `.env`，YAML 中用 `${VAR}` 引用；`nezha init` 自动生成 `.env.example`
- 全局配置目录使用 `~/.nezha/`
