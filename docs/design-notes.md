# Agent Executor 设计笔记

## 1. Agent 间交付物传递机制

### 当前方案：File + Orchestrator

Agent 之间不直接通信，由 Orchestrator（DAG Engine）通过共享 workspace 的文件进行中转。

**当前交付物清单：**

| 交付物 | 生产者 | 消费者 | 格式 |
|--------|--------|--------|------|
| task_list.json | Agent / DAG engine | DAG engine / Agent | JSON |
| .dag_context.json | DAG engine | Agent | JSON |
| execution-report.md | report module | VibeCoding handoff | Markdown |
| progress.md | Agent | 人 | Markdown |
| 源代码文件 | Agent | 下一轮 Agent | 源码 |

### 架构演进路径

**阶段 1（当前）- 单 Agent 单机**
- 传输介质：文件（shared workspace）
- 调度：DAG Engine 线性调度
- 追溯：Git commit = artifact manifest

**阶段 2（未来）- 多 Agent 单机**
- 引入 Pipeline Stage 概念
- 抽象 ArtifactStore 接口，FileStore 实现
- Orchestrator 负责 stage 间翻译和路由

```
pipeline:
  - stage: product
    agent: product-agent
    output: prd.md, task_list.json
  - stage: coding
    agent: coding-agent
    input: task_list.json
    output: source code + tests
  - stage: testing
    agent: testing-agent
    input: source code
    output: test-report.md
```

**阶段 3（远期）- 多 Agent 分布式**
- ArtifactStore 适配 S3 / Redis / MQ
- Agent 可部署在不同机器
- 上层代码不用改，只换适配器

### ArtifactStore 抽象接口（备忘）

```python
class ArtifactStore(Protocol):
    def put(self, key: str, data: bytes | str) -> None: ...
    def get(self, key: str) -> bytes | str | None: ...
    def exists(self, key: str) -> bool: ...
    def list(self, prefix: str) -> list[str]: ...
```

实现：
- `FileStore` — 读写 workspace 文件（现在用）
- `S3Store` — 云存储（以后）
- `RedisStore` — 分布式缓存（以后）

### 设计决策

- **不过早抽象**：当前 file 读写散落在各模块中，等真正需要第二种 agent 协作时再提取 ArtifactStore
- **Orchestrator 模式优于点对点**：Agent 不需要知道彼此存在，orchestrator 做翻译和路由
- **人可以在任意 stage 插入**：PRD review、code review 等环节自然融入 pipeline

---

## 2. Phase 4 执行记录

5 个 task 全部由 Opus 4.6 自动实现，172 个测试通过。

| Task | Turns | Cost | Duration |
|------|-------|------|----------|
| T-001 Verifier | 60 | $2.53 | 7m12s |
| T-002 Execution Report | - | timeout | ~10m |
| T-003 VibeCoding Handoff | 39 | $1.78 | 5m57s |
| T-004 Cost Circuit Breaker | 41 | $2.04 | 5m40s |
| T-005 Knowledge Injection | 33 | $1.51 | 4m29s |

**总计：~$7.86，约 33 分钟**

### 已知问题
- claude-code-sdk 的 `RuntimeError: Attempted to exit cancel scope in a different task` 是已知 SDK bug，不影响功能
- Session timeout 600s 对复杂 task 可能不够，T-002 超时但仍完成了代码提交

---

## 3. 集成测试自动修复循环 (Post-Task Test Cycle)

### 问题

DAG 引擎逐个完成 task 并用 `verification_command` 跑单元测试，但 **DAG 全部通过后缺少集成/E2E 测试环节**。模块各自通过单元测试，不代表它们组合在一起能工作。

### 设计决策

**不引入独立 test-agent**。集成测试是确定性命令（如 `./mvnw verify`），不需要 LLM。修复阶段复用 coding-agent + 专用 fix prompt（`coding/fix.md`），避免新 agent 类型的复杂度。

### 流程

```
DAG all_done (所有 task 单元测试通过)
    ↓
运行集成测试命令 (deterministic, subprocess)
    ├─ PASS → 正常走 git commit/push → Feature COMPLETED
    └─ FAIL → 写 .test_report.json
              → coding-agent fix session (single_round, fix.md prompt)
              → cycle++
              → 重新运行集成测试
              → cycle >= max_cycles → Feature FAILED
```

### 配置

```yaml
# agent.yaml
pipeline:
  post_task_test:
    enabled: true
    command: "./mvnw verify -pl integration-tests"
    max_cycles: 3    # 最多修复 3 轮
    timeout: 600     # 单次测试超时 (秒)
```

默认 `enabled: false`，对现有用户零影响。

### 关键实现

- **`nezha/testing/integration.py`** — `run_test_command()` 执行测试命令，`write_test_report()` 写 `.test_report.json`
- **`nezha/executor.py`** — 在 multi_round 成功后、git commit 前注入循环
- **`coding/fix.md` / `fix.zh.md`** — Fix agent 专用 prompt，聚焦集成问题（模块接线、API 契约、配置错误）
- Fix session 通过 `_run_isolated_session()` 在子进程中运行，复用现有 session 基础设施
- `.test_report.json` 包含 `previous_fixes` 字段，防止 AI 重复相同的失败方案

### 预留扩展

`post_task_test` 未来可加 `agent: "ops-agent"` + `prompt: "ops/verify.md"` 字段，支持 LLM 驱动的验证模式（遗留代码、无法用确定性命令验证的场景）。当前只实现 `command` 模式。

---

## 4. TDD 策略 — AI 编码代理的测试驱动开发

### 问题

AI 编码代理如果先写实现再写测试，会出现两个问题：
1. **自己批自己的作业** — AI 只会写通过当前实现的测试，而非验证需求的测试
2. **垃圾测试** — 测试构造函数、getter/setter 等零价值代码，浪费时间和 token

### 核心原则

**测试必须基于验收标准 (acceptance criteria)，不基于代码结构。**

判断准则："如果这个测试失败了，说明什么业务出了问题？"——答不上来就不要写。

### 步骤顺序 (所有语言通用)

```
1. Read — 读取现有代码了解约定
2. RED — 基于验收标准写失败测试
3. Run tests — 确认失败（尚无实现）
4. GREEN — 实现代码让测试通过
5. Run tests — 确认通过
6. Update state + Commit
```

关键：**步骤 2 (测试) 在步骤 4 (实现) 之前**。之前的 prompt 把实现放在测试前面，导致 TDD 名存实亡。

### 后端 (Java/Spring Boot) 测试策略

**必须测试**：业务规则、边界条件、API 契约、有逻辑的数据转换

**禁止测试**：构造函数/getter/setter、无逻辑 CRUD、Spring 框架行为、无逻辑 DTO 映射、纯 Bean 配置类

**测试分层**：
- Service 层：Mockito 单元测试
- Controller 层：`@WebMvcTest` + MockMvc
- 集成测试：仅关键流程用 `@SpringBootTest`

### 前端 (React/Vue) 测试策略

**核心理念**（Kent C. Dodds Testing Trophy）：
> "Write tests. Not too many. Mostly integration."

**必须测试**：用户交互流程、条件渲染、表单验证、异步操作结果、错误边界

**禁止测试**：组件内部 state、CSS 类名/DOM 结构、props 传递、第三方库行为、纯展示组件、像素值

**测试方法**：
- 用 `getByRole`/`getByText`/`getByLabelText` 查询元素（用户视角，非实现细节）
- 用 `userEvent` 模拟交互（非 `fireEvent`）
- 用 MSW mock API（网络层拦截，非 mock fetch）
- 以集成测试为主（页面/功能级），不逐个组件写单元测试

**工具栈**：Vitest + Testing Library + MSW + jsdom（init prompt 中自动搭建）

### 已知限制：上下文污染

同一个 AI session 既写测试又写实现，存在"上下文污染"风险 — 测试编写阶段的分析会渗透到实现阶段，AI 不自觉地"作弊"。

**当前缓解**：通过 prompt 中明确的步骤分离 (RED → confirm FAIL → GREEN) 来约束。

**未来方向**：可用子代理隔离 RED/GREEN/REFACTOR 三个阶段到不同 session，彻底消除上下文污染。需要 executor 层支持单 task 内的多 session 编排。

### 相关文件

| 文件 | 内容 |
|------|------|
| `prompts/java/worker.md` / `.zh.md` | Java TDD 步骤 + 必须/禁止测试清单 |
| `prompts/python/worker.md` / `.zh.md` | Python TDD 步骤 + pytest 模式 + 必须/禁止测试清单 |
| `prompts/frontend/worker.md` / `.zh.md` | 前端 TDD 步骤 + Testing Library 方法论 |
| `prompts/frontend/init.md` / `.zh.md` | 前端测试基础设施搭建（Vitest + Testing Library + MSW） |
| `prompts/coding/fix.md` / `.zh.md` | 集成测试修复 prompt |

---

## 5. V2 架构方向 — 从角色模拟到上下文拓扑

### 问题

当前架构沿用人类团队分工模型：product-agent → planner-agent → coding-agent → ...，本质上是《人月神话》的 AI 翻版。但业界研究和实践表明，这种"角色模拟"模式对 AI 并非最优。

### 关键证据

**Google DeepMind + MIT 量化实验**（180 种 Agent 配置）：
- 编码是顺序推理任务，多 Agent 性能下降 39-70%
- 单 Agent 准确率 >45% 时，加 Agent 产生递减甚至负面回报
- 独立 Agent 间错误放大 17.2 倍

**Vercel 实践**：删除 80% 的工具，成功率从 80% → 100%，速度快 3.5 倍

**OpenAI Harness Engineering**：零手写代码构建百万行产品，核心是"给 Agent 一张地图而非千页说明书" + 约束驱动

**Anthropic Research 系统**：多 Agent 比单 Agent 高 90%，但成功的关键是**任务并行**（信息收集），不是**角色分工**

**Phil Schmid "苦涩教训"**：Manus 重构 5 次，LangChain 重构 3 次 — "为删除而构建，下一个模型更新会替代你的逻辑"

### 三个范式转变

| | V1（当前） | V2（目标） |
|--|-----------|-----------|
| **第一公民** | Agent 角色（java-agent, product-agent） | 可执行 Step（phase + tech stack） |
| **能力来源** | 角色身份 prompt（"你是 Java 专家"） | 上下文模块组合（phase + stack + concerns） |
| **调度方式** | Push（人手动 `nezha run`） | Pull（daemon 自动拉取 ready step） |

### 核心设计原则

**1. 上下文隔离 > 角色隔离**

```
角色隔离（V1，不好）：PM Agent → Dev Agent → QA Agent
上下文隔离（V2，好）：同一个 worker，不同 session，控制信息可见性

TDD 例：
  session 1: 需求分析 → 写测试（RED）    ← 不知道实现方案
  session 2: 读测试 → 写实现（GREEN）    ← 只看测试，不看 session 1 思考
  session 3: 读全部 → review/refactor
```

**2. Prompt 从"角色身份"变为"上下文模块"**

```
V1: prompts/java/worker.md          ← 整个身份绑定
V2: phases/red.md + stacks/java.md + concerns/tdd.md  ← 按需组合
```

**3. 两层 DAG**

- **Feature DAG**（大需求阶段，人定义/审核）：design → arch → backend → frontend → test
- **Task DAG**（编码任务，AI 自治）：就是现在的 `task_list.json`，AI 自己分析依赖和执行顺序

人审核 task list 的**自然语言描述**，AI 自主规划执行 DAG。

**4. Helper = 控制面，不是角色**

Helper 不是 pipeline 中的节点，而是人和系统之间的对话界面（类似 kubectl）：创建任务、审核通过、查询状态、调整优先级。

### 参考资料

| 来源 | 关键结论 |
|------|---------|
| [Google/MIT: Scaling Agent Systems](https://arxiv.org/html/2512.08296v1) | Agent 扩展三定律，编码任务多 Agent 性能下降 |
| [OpenAI: Harness Engineering](https://openai.com/index/harness-engineering/) | 给 Agent 地图不给百科全书，约束驱动速度 |
| [Anthropic: Building Effective Agents](https://www.anthropic.com/research/building-effective-agents) | 从简单开始，找尽可能简单的方案 |
| [Anthropic: Multi-Agent Research System](https://www.anthropic.com/engineering/multi-agent-research-system) | 多 Agent 有效的前提是任务天然可并行 |
| [Vercel: Removed 80% of Tools](https://vercel.com/blog/we-removed-80-percent-of-our-agents-tools) | 减法带来增加 |
| [Phil Schmid: Agent Harness 2026](https://www.philschmid.de/agent-harness-2026) | Harness = OS，为删除而构建 |
| [Manus: Context Engineering](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus) | 文件系统即上下文，通过复述操纵注意力 |
| [Spotify: Background Coding Agents](https://engineering.atspotify.com/2025/11/context-engineering-background-coding-agents-part-2) | 一次一个变更，限制工具，让 Agent 自己想办法 |
| [Armin Ronacher: Agentic Coding](https://lucumr.pocoo.org/2025/6/12/agentic-coding/) | 函数代替类，原生 SQL 代替 ORM，让 AI 做最蠢但有效的事 |

### 与 Claude Agent Teams 的关系

Agent Teams（2026.2.6 随 Opus 4.6 发布，实验性）是 Claude Code 内的多实例并行协作机制，与 agent-executor 是不同层次的东西：

| | Agent Teams | agent-executor |
|--|-------------|----------------|
| 层次 | Session 内协作（多核 CPU） | 跨 Session 生命周期管理（OS） |
| 状态持久 | 无（session 结束即丢失） | 有（progress, task_list, feature.yaml） |
| 人工审核 | 无机制 | Review gate, approve/reject |
| 质量保障 | 无 | 集成测试循环、TDD、自动修复 |
| 项目知识 | 无 | project/, standards/, CLAUDE.md |

**结论**：互补而非竞争。V2 在 Worker 执行层预留 `ExecutionStrategy` 接口，当前用单实例（`SingleInstanceStrategy`），Agent Teams 稳定后加 `AgentTeamsStrategy` 实现即可接入。详见 [vision.md §17.8](vision.md)。

---

## 6. 外部记忆系统 — 预留策略

### 问题

Agent 的记忆是 session 级的 — task 结束，经验丢失。反复踩同样的坑（依赖冲突、API 用法、代码风格偏好），每次从零开始。

### 现状评估

当前文件系统已覆盖 ~80% 的记忆场景：

- `CLAUDE.md` / `project/standards/` — 永久规范（手动/evolve-agent 维护）
- `rework_note`（结构化 JSON）— feature 级失败经验（已尝试/未尝试）
- `progress.md` — feature 级执行进度
- `project/knowledge/` — 共享知识库

**缺口**（剩余 20%）：跨 feature 经验积累、失败模式学习、隐性偏好记忆。

### 设计决策

**不现在引入外部记忆系统。** 三层递进，按需升级：

| 层次 | 方案 | 触发条件 |
|------|------|---------|
| L0（当前） | 文件系统 — CLAUDE.md, rework_note | 已实现 |
| L1（轻量） | `failure_log.jsonl` + 关键词检索 | Daemon 模式上线后，跨 feature 经验价值凸显 |
| L2（外部） | EverMemOS / Mem0 via MCP | L1 关键词检索不够，需要语义检索 |

### 预留接口

`MemoryStore` 协议（`remember` / `recall` / `forget`），注入点在 `build_context()` 中。详见 [vision.md §17.9](vision.md)。

### 候选系统

- **EverMemOS**：多层记忆架构（感知/短期/长期/元认知），自动归纳，MCP 接入
- **Mem0**：轻量级，向量搜索，SaaS + 自托管，REST API / MCP 接入

**原则**：与 Agent Teams 策略一致 — 预留接口，不急着集成。文件系统是当前最好的记忆系统。

---

## 7. Prompt 模块化 (Phase A)

### 问题

每个 agent 角色（java、python、frontend、evolve）各有一份完整的 worker.md，内容 ~80% 重复。新增 agent 角色需要 copy-paste 整个 prompt。

### 方案 — PromptComposer

将 prompt 拆为 base（角色声明）+ sections（可插拔模块），Agent YAML 通过 `session.compose` 配置组合：

```yaml
session:
  compose:
    worker:
      base: "coding/base.md"
      sections:
        - phases/context-acquisition
        - phases/rework
        - stacks/java-spring
        - phases/tdd
        - phases/regression
```

### 模块分类

| 类型 | 模块 | 说明 |
|------|------|------|
| phases/ | context-acquisition, rework, tdd, regression, commit-rules | 工作流阶段 |
| stacks/ | java-spring, python, frontend, general | 技术栈知识 |
| concerns/ | exec-plan, quality-tracking | 横切关注点 |

### 关键实现

- `pipeline/prompt_composer.py` — `compose_prompt()` 复用 `resolve_prompt_path()` 做 4 层 locale 查找
- `config.py` — `ComposeConfig(base, sections)` + `SessionConfig.compose: dict[str, ComposeConfig]`
- 向后兼容：无 `session.compose` 配置时走原有 `session.prompts.worker` 路径

### 相关文件

| 文件 | 内容 |
|------|------|
| `pipeline/prompt_composer.py` | `compose_prompt()` 实现，基于 base + sections 组装 prompt |
| `config.py` | `ComposeConfig` dataclass + `SessionConfig.compose` 字段 |
| `templates/prompts/coding/base.md` | 通用角色声明（所有 coding agent 共享） |
| `templates/prompts/modules/phases/` | 工作流阶段模块（context-acquisition, rework, tdd, regression, commit-rules） |
| `templates/prompts/modules/stacks/` | 技术栈模块（java-spring, python, frontend, general） |
| `templates/prompts/modules/concerns/` | 横切关注点（exec-plan, quality-tracking） |
