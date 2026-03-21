# Agent Executor — 待做任务

> 详细设计见 [docs/vision.md](docs/vision.md) 对应章节。

---

## 阶段 5：Harness 质量增强

### P1（近期）

- [x] **结构化 rework_note**（vision.md §14.2）✅
  - `Task.rework_note` 改为 `str | dict`，向后兼容
  - `verifier.py` 写入结构化 dict `{attempt/tried/not_tried/related_files/block_reason}`
  - `build_dag_context()` 透传 dict（JSON 自然嵌套）
  - `engine.py` console 输出结构化展示
  - `prompts/evolve/worker.md` 更新 rework 指南

- [x] **exec-plan.md 一等交付物**（vision.md §14.3）✅
  - `report.py` 新增 `generate_exec_plan()` / `write_exec_plan()`
  - `engine.py` 初始加载 + 每轮 session 后更新
  - `session.py` 在 task_list.json 就绪后立即生成
  - `prompts/evolve/worker.md` 加入 exec-plan.md 读取指引

### P2（中期）

- [x] **quality.md 代码质量评分**（vision.md §14.4）
  - 在 `workspace/project/` 新增 `quality.md`（各模块评分 + 技术债记录）
  - evolve-agent 每次执行后（或 gardening mode）更新此文件

- [x] **Git Worktree 集成**（vision.md §14.5）
  - `GitConfig` 新增 `use_worktree: bool = False`
  - executor 在 `branch_per_task=True` + `use_worktree=True` 时自动 `git worktree add`
  - task 完成后 `git worktree remove`
  - worktree 路径写入 `task.yaml` 的 `metadata.worktree_path`
  - 消除 `_check_coding_safety()`（worktree 天然隔离）

- [x] **evolve-agent 文档园丁模式**（vision.md §14.6）
  - 新增 `--mode gardening` 参数（`__main__.py` + executor）
  - gardening 职责：文档陈旧检测、技术债追踪、架构约定核查、测试覆盖率、CLAUDE.md 同步
  - 支持 cron 调度触发

- [x] **pm-agent 跨 Agent 协调**（vision.md §14.7）
  - 场景 5：上游 task 完成 → 自动为下游 Agent 创建 task
  - 场景 6：task FAILED 时质量仲裁（重试 vs 人工介入）
  - 场景 7：定时健康巡检（成功率、耗时、rework 次数统计）
  - 更新 pm-agent prompt（`prompts/pm/worker.md`）

---

## 阶段 6：调度优化 + 并行

### P1（近期）

- [x] **Feature 优先级调度**（vision.md §15.2）
  - `Feature` dataclass 新增 `priority: int = 50`（0-100）
  - `feature.yaml` 新增 `priority` 字段
  - `FileFeatureQueue.get_next()` 改为按 `(-priority, created_at)` 排序
  - CLI `feature create` 新增 `--priority` 参数

- [x] **调度器自适应退避**（vision.md §15.3）
  - `ContinuousScheduler` 增加 `_consecutive_failures` 计数 + 指数退避逻辑
  - `executor.yaml` 新增 `scheduler.max_backoff`（默认 3600s）和 `scheduler.backoff_on_no_task`
  - 更新 `ExecutorConfig` 和 `SchedulerConfig` dataclass
  - 相应测试

### P2（中期）

- [x] **并行执行**（vision.md §15.4）✅
  - `executor.yaml` 新增 `scheduler.concurrency`（默认 1）
  - `asyncio.Semaphore(concurrency)` + `asyncio.gather` 并发控制
  - design/planning agent 无需 worktree 也可并行

### P3（远期）

- [ ] **PipelineScheduler Agent DAG**（vision.md §15.5）
  - `pipeline.yaml` 格式草案：stages / input_from / parallel
  - PipelineScheduler 读取依赖图，监听 task 完成事件触发下游

---

## 阶段 6.5：Direct API 模式

### P1（近期，改动小）

- [x] **planner-agent Direct 模式**（vision.md §16）
  - `EngineConfig.api_type: "anthropic" | "openai"`（双协议，同 nl2cypher 模式）
  - `pipeline/direct_api.py` 新增 `run_direct_api()`（调用 anthropic / openai SDK）
  - executor 分支：`session.mode == "direct"` → 走 run_direct_api()，否则走原有路径
  - planner-agent.yaml 改为 `session.mode: "direct"`, `engine.api_type: "anthropic"`
  - 预期：planner 执行时间从 ~7-15s 降至 ~3-8s

---

## 运行时健壮性增强

- [x] **`--at` / `--delay` 定时/延迟执行** ✅
  - `delay.py` 新增 parse_delay / parse_at / wait_until_ready
  - `__main__.py` run 子命令新增 --at / --delay 参数

- [x] **Worktree 崩溃恢复** ✅
  - 启动时检测残留 worktree 并自动清理

- [x] **Session 子进程清理** ✅
  - `_kill_process_group()` 确保子进程组完全终止

- [x] **Feature↔Task 重命名** ✅
  - 符合 PM 语义：Feature（大需求）> Task（小编码任务）
  - 代码、测试、prompt、i18n、CLI 全量替换（780 测试通过）

---

## 阶段 6.8：V2 Phase A — Prompt 模块化

### P1（已完成）

- [x] **PromptComposer 组合系统** ✅
  - `config.py` 新增 `ComposeConfig` dataclass + `SessionConfig.compose`
  - `pipeline/prompt_composer.py` 新增 `compose_prompt()`
  - `session.py` run_single_round / _SUBPROCESS_RUNNER / _VIBE_SUBPROCESS_RUNNER 加 compose 分支
  - 向后兼容：无 compose 配置时走原有 prompts.worker 路径

- [x] **Prompt 模块库** ✅
  - `templates/prompts/modules/phases/` — context-acquisition, rework, tdd, regression, commit-rules
  - `templates/prompts/modules/stacks/` — java-spring, python, frontend, general
  - `templates/prompts/modules/concerns/` — exec-plan, quality-tracking
  - `templates/prompts/coding/base.md` — 通用角色声明模板

---

## 阶段 6.9：V2.0 + V2.1（已完成）

### V2.0（已完成）

- [x] **F1: 链式分支** ✅ — `--base-branch` 支持分支链式创建
- [x] **F2: per-Task model** ✅ — task_list.json `"model"` 字段覆盖 engine model
- [x] **F3: 集成验证** ✅ — DAGEngine integration_prompt_path + 额外集成 session
- [x] **F4: Feature Steps** ✅ — FeatureStep 数据结构 + approve/reject CLI + DAG 依赖
- [x] **F5: 费用统计** ✅ — `feature show` 费用摘要 + `feature list` COST 列 + `_parse_report_summary()`

### V2.1（已完成）

- [x] **F1: Helper 全能化** ✅ — 5→9 场景（分析+操作），callable=true，Write 工具
- [x] **F2: Dashboard** ✅ — `nezha dashboard` 生成自包含静态 HTML（状态+费用可视化）
- [x] **F3: 并行执行** ✅ — `scheduler.concurrency` + asyncio.Semaphore 并发控制

---

## V2.3：Claude Code 原生集成 + AI Judge 多模型（已完成）

- [x] **F1: Claude Code 集成** ✅ — `nezha init` 生成 CLAUDE.md + `.claude/skills/` + `settings.json`
  - 16 个交互技能（EN/ZH 国际化）：status/feature-list/prd/architecture/review/rework/batch-features 等
  - CLAUDE.md 通过 `@` 语法导入 executor.yaml 和 agents/*.yaml
  - `!`cmd`` 动态注入限制为单条命令（兼容 Claude Code 权限检查）
  - 已有项目 re-init 只重新生成 .claude/ 配置，不覆盖原有文件

- [x] **F2: AI Judge 多模型** ✅ — `failure_strategy: "ai_judge"` 支持 Anthropic + OpenAI 兼容 API
  - `SchedulerConfig` 新增 `judge_model` / `judge_api_type` / `judge_env`
  - `executor.py` 新增 `_judge_call_anthropic()` / `_judge_call_openai()` 双协议分发
  - 支持 GLM / Kimi / MiniMax 等 OpenAI 兼容 API

- [x] **F3: slugify ASCII-only** ✅ — Feature ID / branch 只含 ASCII
  - `_slugify()` 正则改为 `[^a-z0-9_\s-]`，中文/特殊字符被过滤
  - `Feature.title` 保留原始文本供显示（不再用 slug 覆盖）

---

## 阶段 7：生态扩展（远期）

- [ ] **代码分析系统桥接**
  - `knowledge_source` 配置接入 Neo4j/Qdrant 查询结果
  - 将 legacy Java/Spring 代码分析结果注入 agent context（CLAUDE.md / agent-context.md）

- [ ] **reviewer-agent**
  - coding agent 完成后自动触发 code review
  - 产出 review-report.md，支持 inline 评论格式

- [ ] **browser-tool / log-tool**（Application Legibility）
  - browser-tool：通过 Chrome DevTools Protocol 让 Agent 直接读取 UI 状态
  - log-tool：LogQL / PromQL 查询接入，让 Agent 读取运行日志和指标

---

## 阶段 8：分布式（远期）

- [ ] **RedisFeatureQueue**：FeatureQueue Protocol 的 Redis 实现（接口不变）
- [ ] **S3ArtifactStore**：ArtifactStore Protocol 的 S3 实现
