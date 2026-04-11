# Harness Engineering 参考指南

> 本文档整理自 OpenAI、Anthropic 等顶级 AI 机构的公开博客和研究，用于指导 nezha 项目的演进方向。

## 目录

- [什么是 Harness Engineering](#什么是-harness-engineering)
- [核心理念](#核心理念)
- [Agent Loop 架构](#agent-loop-架构)
- [Harness 关键组件](#harness-关键组件)
- [Anthropic：构建高效 Agent 的模式](#anthropic构建高效-agent-的模式)
- [OpenAI：Codex Harness 工程实践](#openaicodex-harness-工程实践)
- [IMPACT 框架](#impact-框架)
- [与 nezha 的差距分析](#与-nezha-的差距分析)
- [参考资料](#参考资料)

---

## 什么是 Harness Engineering

**Harness Engineering** 是围绕 LLM 构建可靠 Agent 系统的工程学科。核心关注点不是模型本身，而是模型外围的一切——工具调度、上下文管理、权限控制、错误恢复、状态持久化。

> "Model = CPU, Context window = RAM, Harness = Operating System, Agent = Application"
> — Phil Schmid

类比：如果 LLM 是 CPU，那 Harness 就是操作系统——它负责进程调度、内存管理、I/O 抽象和安全隔离。模型能力决定上限，但 Harness 决定这个上限能不能稳定达到。

**为什么重要**：Meta 以约 $2B 收购 Manus，买的不是模型，而是 Harness。Manus 在 6 个月内重建了 5 次 Harness。

---

## 核心理念

### 1. 人类掌舵，Agent 执行（Humans steer, Agents execute）

来源：[OpenAI Harness Engineering](https://openai.com/index/harness-engineering/)

工程师的角色从「写代码」转变为：
- **设计环境和抽象**——而非实现细节
- **构建反馈循环**——而非手动调试
- **移除 Agent 障碍**——当进度停滞时，问的不是"再试试"，而是"缺少什么能力？"

OpenAI 的 Codex 团队用这种方式在 5 个月内生成了约 100 万行生产代码（1500+ PR），**零手写代码**。

### 2. Agent 可读性是设计目标（Agent Legibility）

来源：[OpenAI Harness Engineering](https://openai.com/index/harness-engineering/)

> "Anything the agent can't access effectively doesn't exist."

- 信息必须在代码仓库内可发现——Slack、Google Docs、人脑中的知识对 Agent 不存在
- 仓库结构为 Agent 理解力优化，而非人类审美
- 文档是机器可读的一等公民

### 3. 给 Agent 地图，而非说明书（Maps over Manuals）

来源：[OpenAI Harness Engineering](https://openai.com/index/harness-engineering/)

> "Give Codex a map, not a 1,000-page instruction manual."

- 放弃巨型 AGENTS.md/CLAUDE.md 指令文件（挤占上下文、信噪比低、难以验证）
- 用 **~100 行的入口文件**指向结构化的 `docs/` 目录
- 渐进式披露（progressive disclosure）：Agent 从入口开始，按需深入

### 4. 约束前置，纠错便宜（Architectural Constraints Early）

来源：[OpenAI Harness Engineering](https://openai.com/index/harness-engineering/)

> "Architecture you'd normally postpone until scale becomes an early prerequisite for agent velocity."

- 架构约束通过自定义 linter 和结构化测试强制执行
- 规则不是文档描述，而是**代码验证**
- 当文档不够时，将规则提升为 lint 规则

### 5. 纠错便宜，等待昂贵（Corrections are cheap, waiting is expensive）

来源：[OpenAI Harness Engineering](https://openai.com/index/harness-engineering/)

- 最小化阻塞式合并门禁
- 短生命周期 PR
- 测试 flake 用后续 run 修复，而非无限阻塞
- Agent 吞吐量下，传统 CI 实践可能适得其反

---

## Agent Loop 架构

### ReAct 循环（READ → PLAN → ACT → OBSERVE）

所有主流 Agent 系统共享的核心模式：

```
┌─────────────────────────────────────────────┐
│                 Agent Loop                   │
│                                             │
│   ┌──────┐    ┌──────┐    ┌─────┐          │
│   │ READ │───▶│ PLAN │───▶│ ACT │          │
│   └──────┘    └──────┘    └─────┘          │
│       ▲                       │             │
│       │      ┌─────────┐     │             │
│       └──────│ OBSERVE │◀────┘             │
│              └─────────┘                    │
│                                             │
│   循环直到：任务完成 / 达到上限 / 人工介入    │
└─────────────────────────────────────────────┘
```

1. **READ** — 收集上下文（文件、测试输出、错误信息）
2. **PLAN** — 模型生成推理轨迹和下一步动作
3. **ACT** — 通过工具调度执行（编辑文件、运行命令、调 API）
4. **OBSERVE** — 评估结果，决定重试 vs. 升级

> "You absolutely have to test what it writes." — Addy Osmani
>
> 没有测试反馈的 Agent 会幻觉自己的进度。

### 错误恢复层级

| 级别 | 策略 | 说明 |
|------|------|------|
| L1 | 带上下文重试 | Agent 调整方法再试 |
| L2 | Git checkpoint 回滚 | 回到已知好状态 |
| L3 | 任务分解给子 Agent | 降低单次复杂度 |
| L4 | 升级给人类 | 附带尝试记录和失败分析 |

---

## Harness 关键组件

综合 OpenAI Codex Harness、Anthropic Claude Code、SWE-agent 的架构：

### 1. 上下文工程（Context Engineering）

**三层压缩策略**（来自 Claude Code 架构分析）：

| 策略 | 触发条件 | 方法 | 成本 |
|------|---------|------|------|
| MicroCompact | 持续进行 | 局部编辑缓存内容 | 零 |
| AutoCompact | 上下文窗口 70% | 20K token 结构化摘要 | 低 |
| Full Compact | 紧急情况 | 整个对话 + 选择性重注入 | 高 |

**上下文注入策略**：
- **CLAUDE.md / AGENTS.md** — 项目级规则，每个 session 加载
- **Just-in-Time 检索** — 轻量引用，按需加载（MCP 可减少 95% 上下文）
- **文件系统即扩展内存** — 大型输出存到文件，保留可恢复引用
- **子 Agent 隔离** — 每个子 Agent 独立上下文窗口

**上下文腐烂问题**：模型准确度随 token 增加而下降（transformer 的 n² 注意力开销），因此上下文管理是核心竞争力。

### 2. 工具注册与权限系统（Tool Registry & Permission）

**设计原则**（来自 Claude Code）：

> "Never create a general-purpose tool when a specific one will do."
> "Define your tools as narrowly as possible, document their constraints inside the tool definition."

- 工具约束写在工具定义里（模型在调用时能看到），而非远处的系统提示
- 权限分层：读操作自动放行，写操作需确认，高危操作（force push、rm -rf）需显式授权
- Git 安全示例：Bash 工具描述中禁止 `push --force`、`reset --hard`

### 3. 模型路由（Model Routing）

按任务复杂度路由模型（来自 Claude Code 实践）：

| 任务类型 | 模型选择 | 说明 |
|---------|---------|------|
| 分类 & 安全检查 | Haiku（快/便宜） | 不需要深度推理 |
| 标准编码工作 | Sonnet（均衡） | ~80% 的 Agent 调用 |
| 深度推理 & 规划 | Opus（强/贵） | 架构决策、复杂算法 |

> ~80% 的 Agent 调用不需要前沿模型，合理路由可大幅降低成本。

### 4. 沙盒与隔离（Sandbox & Isolation）

| 系统 | 隔离方式 |
|------|---------|
| OpenAI Codex | 云端容器，每任务独立 |
| SWE-agent | Docker 容器 + 自定义 shell |
| Claude Code | 子进程 + 权限钩子 |
| Devin | 完整 VM 沙盒 |
| **nezha** | Python 子进程 + Git worktree |

### 5. 线程与状态管理（Thread Management）

来自 [OpenAI Codex Harness](https://openai.com/index/unlocking-the-codex-harness/)：

Codex 的核心抽象是 **Thread**（线程）：
- **创建 / 恢复 / Fork / 归档** — 完整生命周期管理
- **事件持久化** — 客户端断连后可重新连接，渲染一致的时间线
- 统一协议（JSON-RPC）— CLI、IDE、Web、桌面端共享同一个 Harness

### 6. 熵管理（Entropy Management）

来源：[OpenAI Harness Engineering](https://openai.com/index/harness-engineering/)

Agent 会复制已有模式——包括坏的。解决方案不是周五手动清理，而是：

1. 将「黄金原则」编码到仓库中
2. 创建定期扫描的清理 Agent
3. 自动提交重构 PR
4. 像 GC 一样持续小额偿还技术债

---

## Anthropic：构建高效 Agent 的模式

来源：[Building Effective Agents](https://www.anthropic.com/research/building-effective-agents)（2024.12）

### 核心区分：Workflow vs. Agent

| | Workflow | Agent |
|---|---------|-------|
| 控制流 | 预定义代码路径 | 模型动态决策 |
| 适用场景 | 可预测的结构化任务 | 开放式、步数不确定的任务 |
| 权衡 | 一致性和可预测性 | 灵活性，但延迟/成本更高 |

### 五种 Workflow 模式

```
1. Prompt Chaining（提示链）
   A → B → C（每步处理上一步输出）

2. Routing（路由）
   Input → [分类器] → 专用处理器 A/B/C

3. Parallelization（并行化）
   Input → [A, B, C 并行] → 汇总

4. Orchestrator-Workers（编排-工人）
   Orchestrator → 动态拆解 → Workers → 汇总

5. Evaluator-Optimizer（评估-优化）
   Generator ↔ Evaluator（循环改进直到达标）
```

### Agent 设计三原则

1. **简洁** — 保持设计简单，不要过度工程
2. **透明** — 显式展示规划步骤和决策过程
3. **ACI 质量** — 像设计提示词一样认真设计工具接口

### 关键建议

- **先用 LLM API 直接调用**，再考虑框架
- 很多模式只需几行代码就能实现
- 框架会遮蔽底层机制，导致调试困难
- **工具设计和提示词设计同等重要**——站在模型角度思考
- 使用 poka-yoke（防错）原则——用绝对路径替代相对路径，减少犯错机会

---

## OpenAI：Codex Harness 工程实践

来源：
- [Harness Engineering](https://openai.com/index/harness-engineering/)（2026.01）
- [Unlocking the Codex Harness](https://openai.com/index/unlocking-the-codex-harness/)（2026.02）
- [Unrolling the Codex Agent Loop](https://openai.com/index/unrolling-the-codex-agent-loop/)（2026.01）

### 文档即系统记录

```
docs/
├── design-docs/        # 设计文档（附验证状态）
├── exec-plans/         # 执行计划（附决策日志）
├── generated/          # 自动生成的工件（如 db-schema.md）
├── product-specs/      # 产品规格
├── references/         # 技术参考（供 Agent 学习）
├── DESIGN.md
├── FRONTEND.md
├── QUALITY_SCORE.md
├── RELIABILITY.md
└── SECURITY.md
```

### 关键实践

| 实践 | 说明 |
|------|------|
| **AGENTS.md 约 100 行** | 只做目录索引，指向 docs/ 下的详细文档 |
| **Linter 验证文档** | CI 检查文档是否与代码一致，是否过期 |
| **错误信息注入修复指令** | 自定义 lint 的错误消息直接告诉 Agent 怎么修 |
| **环境可检视** | Chrome DevTools Protocol 让 Agent 能驱动 UI 测试 |
| **Per-worktree 可启动** | 每个变更有独立的应用实例 |
| **可观测性即上下文** | Log(LogQL)、Metrics(PromQL)、Traces 暴露给 Agent |

### 成果数据

- **3.5 PRs/工程师/天**（随团队增长保持）
- **约 100 万行代码 / 5 个月**（约传统方式 1/10 时间）
- 单次 Codex 运行可执行 **6+ 小时**

---

## IMPACT 框架

来源：swyx 提出，[MorphLLM 总结](https://www.morphllm.com/agent-engineering)

构建可靠 AI Agent 的六个核心要素：

| 要素 | 含义 | nezha 对应 |
|------|------|-----------|
| **I**ntent | 目标通过评估验证；Agent 行动前必须理解成功标准 | feature.yaml + acceptance criteria |
| **M**emory | 跨 session 的长期一致性——技能库、可复用工作流模式 | agent-context.md + progress.md |
| **P**lanning | 可编辑的多步计划；静态计划失败，自适应计划成功 | task_list.json + DAG |
| **A**uthority | 信任、权限模型、沙盒边界——**最被忽视的要素** | security.py + .claude/settings.json |
| **C**ontrol Flow | LLM 驱动的动态执行路径 vs. 硬编码序列 | DAG engine + scheduler |
| **T**ools | RAG、沙盒执行、浏览器自动化 | engine.py + pipeline/ |

---

## 与 nezha 的差距分析

### 已经具备的能力

| 能力 | nezha 实现 | 对标 |
|------|-----------|------|
| DAG 任务调度 | dag/engine.py + graph.py | OpenAI exec-plans |
| 多 Agent YAML 编排 | config.py + executor.py | Codex harness |
| Feature 生命周期 | feature_queue.py（6 态） | Thread 管理 |
| 子进程隔离 | pipeline/session.py | 容器/沙盒 |
| Git worktree | executor.py worktree 管理 | Per-worktree 可启动 |
| 模型路由 | model_map（complexity → model） | 模型路由 |
| Prompt 模块化 | prompt_composer.py | 上下文工程 |
| 两级验证 | dag/verifier.py | 反馈循环 |
| Guard Rails | guards/（熔断/时间窗/余额） | 安全约束 |
| AI Judge | executor.py ai_judge | 失败决策 |

### 需要补齐的能力

| 能力 | 差距 | 优先级 | 参考 |
|------|------|--------|------|
| **结构化输出** | planner 靠 prompt 约定 JSON，无 schema 强制 | P0 | Anthropic tool_use |
| **上下文压缩** | 无 compaction 机制，长 session 会撑爆上下文 | P0 | Claude Code 三层压缩 |
| **故障自恢复** | Ctrl+C 后需手动改状态，无 checkpoint | P1 | Codex Thread resume |
| **文档即系统记录** | CLAUDE.md 单文件，非结构化 docs/ | P1 | OpenAI docs/ 分层 |
| **熵管理** | 无自动清理 Agent，技术债靠人工 | P1 | OpenAI GC Agent |
| **可观测性** | 静态 HTML Dashboard，无实时流 | P2 | 可观测性即上下文 |
| **E2E 测试** | 937 单元测试，无全流程集成测试 | P2 | SWE-bench harness |
| **工具约束内嵌** | 安全规则在 security.py，非工具定义内 | P2 | Claude Code 工具定义内嵌约束 |
| **多 Agent 通信** | Agent 间通过文件传递，无直接通信 | P3 | Orchestrator-Workers |
| **分布式执行** | 单机文件系统，FileFeatureQueue | P3 | 分布式队列 |

### 近期踩坑 vs. 行业最佳实践

| 我们踩的坑 | 行业对应的最佳实践 |
|---|---|
| planner 写到 target 目录 | **绝对路径代替相对路径**（Anthropic poka-yoke 原则） |
| task_list.json 双引号没转义 | **结构化输出 / tool_use**（不靠 prompt 约定格式） |
| agent 把 passes 写到 worktree | **workspace 路径明确注入 prompt**（Agent 可读性设计） |
| coding agent 找不到 feature | **流程解耦**（queue 过滤不应与后续步骤耦合） |

---

## 下一步演进建议

基于行业实践，nezha 的优先演进路径：

### Phase 1: 可靠性基础（对应 P0）
1. **Planner 结构化输出** — 使用 Anthropic tool_use 强制 JSON schema
2. **上下文压缩** — 参考 Claude Code 三层压缩策略
3. **Session 状态持久化** — checkpoint/resume 机制

### Phase 2: 工程化提升（对应 P1）
4. **结构化 docs/** — 从单一 CLAUDE.md 迁移到分层文档
5. **故障自恢复** — 中断后自动恢复 feature 状态
6. **熵管理 Agent** — 定期扫描和修复代码质量偏差

### Phase 3: 规模化能力（对应 P2-P3）
7. **实时可观测性** — 实时 Dashboard + 结构化日志
8. **E2E 集成测试** — 全流程回归测试
9. **分布式队列** — Redis/MQ 替代文件队列

---

## 参考资料

### OpenAI

- [Harness Engineering: Leveraging Codex in an Agent-First World](https://openai.com/index/harness-engineering/) — 定义了 Harness Engineering 学科，最核心的参考
- [Unlocking the Codex Harness: How We Built the App Server](https://openai.com/index/unlocking-the-codex-harness/) — Codex 统一 Harness 架构（Thread、JSON-RPC、多端共享）
- [Unrolling the Codex Agent Loop](https://openai.com/index/unrolling-the-codex-agent-loop/) — Agent Loop 内部实现、上下文管理、prompt caching
- [Introducing Codex](https://openai.com/index/introducing-codex/) — Codex 产品发布

### Anthropic

- [Building Effective Agents](https://www.anthropic.com/research/building-effective-agents) — 五种 Workflow 模式 + Agent 设计原则，必读
- [Anthropic Cookbook: Agent Patterns](https://github.com/anthropics/anthropic-cookbook/tree/main/patterns/agents) — 参考实现
- [Claude Code SDK](https://docs.anthropic.com/en/docs/claude-code-sdk) — SDK 文档

### 综合分析

- [Agent Engineering: Harness Patterns, IMPACT Framework](https://www.morphllm.com/agent-engineering) — IMPACT 框架 + 行业 Agent 系统对比
- [Production AI Agent Architecture: Lessons from Claude Code](https://artinoid.com/blog/production-ai-agent-architecture-claude-code-lessons) — Claude Code 架构深度分析
- [OpenAI Introduces Harness Engineering (InfoQ)](https://www.infoq.com/news/2026/02/openai-harness-engineering-codex/) — InfoQ 报道

### 学术

- [SWE-bench: Can Language Models Resolve Real-World GitHub Issues?](https://arxiv.org/abs/2310.06770) — 评估基准
- [SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering](https://arxiv.org/abs/2405.15793) — ACI 设计（NeurIPS 2024）
- [Building AI Coding Agents for the Terminal](https://arxiv.org/html/2603.05344v1) — 终端 Agent 架构

---

*最后更新：2026-04-10*
