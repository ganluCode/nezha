# Nezha (哪吒)

> Named after Nezha, the mythical deity with three heads and six arms — symbolizing the framework's ability to orchestrate multiple AI agents working in parallel with efficiency and power.

A YAML-driven AI Agent orchestration and execution framework, built on the [Claude Code SDK](https://docs.anthropic.com/en/docs/claude-code/sdk).

[![Python](https://img.shields.io/badge/Python-≥3.12-blue.svg)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/Tests-933%20passed-brightgreen.svg)](#testing)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Nezha elevates AI coding agents from "chat tools" to a **fully orchestratable, observable, and continuously running engineering system**. Just write YAML configs to drive multiple agents through complex software engineering tasks in DAG dependency order.

---

## Features

- **YAML-Driven Configuration** — Agents, schedulers, guards, and event handlers are all declaratively configured, zero code needed
- **Multi-Agent Collaboration** — coding / planning / management agent types with callable cross-invocation
- **DAG Dependency Scheduling** — task_list.json defines task dependency graphs, auto-executed in topological order
- **Feature Queue** — Requirement-level task queue with state transitions, step-by-step approval, and priority sorting
- **Three Session Modes** — single_round / multi_round (DAG multi-turn) / direct (API direct)
- **Git Automation** — Per-task branch creation, worktree isolation, auto commit/push
- **Guard Chain** — Circuit breaker, balance check, time window — automatic pre-execution interception
- **Event System** — File logging, state tracking, execution traces for full observability
- **Parallel Execution** — asyncio.Semaphore concurrency control, multiple Features executed simultaneously
- **Cost Tracking** — Auto-parse execution-report.md, aggregate session-level costs
- **Static Dashboard** — One-click HTML visualization dashboard generation
- **Prompt Composer** — Modular prompt system: base + phases + stacks + concerns, freely composable
- **model_map Model Mapping** — Auto-select models by task complexity, with multi-vendor API Key/Base URL switching
- **Multi-Model Support** — Native Claude support, third-party model compatibility via `ANTHROPIC_BASE_URL`
- **Claude Code Native Integration** — `init` auto-generates CLAUDE.md + 16 interactive skills (/status, /prd, /review, /rework, etc.), EN/ZH i18n
- **AI Judge Multi-Model** — Failure strategy supports Anthropic + OpenAI-compatible API evaluation

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                     CLI (nezha)                      │
├─────────────────────────────────────────────────────┤
│                                                     │
│   executor.yaml ──→ Executor                        │
│       │                │                            │
│       │          ┌─────┴─────┐                      │
│       │          │ GuardChain│ (breaker/balance/     │
│       │          └─────┬─────┘  time window)        │
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
│                  │ EventBus  │ (logs/state/traces)   │
│                  └───────────┘                      │
│                                                     │
│   workspace/          target/                       │
│   (metadata)          (code repo)                   │
└─────────────────────────────────────────────────────┘
```

---

## Quick Start

### Prerequisites

- Python ≥ 3.12
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) (`claude` command available)
- Git

### Installation

```bash
# 1. Clone the repository
git clone https://github.com/your-org/nezha.git
cd nezha

# 2. Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 3. Create virtual environment + install
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"

# Or use Makefile (equivalent)
make venv && source .venv/bin/activate
make install-dev

# 4. Global install (optional, adds nezha command to PATH)
pipx install .

# 5. Verify
nezha --help
```

### Global User Config (optional, one-time)

```bash
# Create global config directory
mkdir -p ~/.nezha

# Set default preferences (auto-applied when initializing new projects)
cat > ~/.nezha/config.yaml << 'EOF'
locale: "en_US"
timezone: "America/New_York"
model_map:
  low: "claude-sonnet-4-6"
  medium: "claude-sonnet-4-6"
  high: "claude-opus-4-6"
EOF
```

### Initialize a Project

```bash
# 1. Create executor workspace (separate from code repo)
nezha init /path/to/my-project
cd /path/to/my-project

# 2. Configure sensitive variables — copy .env.example and fill in actual values
cp .env.example .env
# Edit .env, fill in ANTHROPIC_API_KEY, GH_TOKEN, etc.

# 3. Configure target code repo — edit executor.yaml
#    Modify: target: "/path/to/your/code-repo"

# 4. Initialize project knowledge base (optional, generates PRD templates etc.)
nezha project init

# 5. Use Claude Code for interactive collaboration (optional)
#    init has auto-generated CLAUDE.md + .claude/skills/
#    Run claude in the project directory, type / to see all skills
claude
```

Post-initialization directory structure:

```
my-project/
├── executor.yaml          # Global config (scheduling, guards, event handlers)
├── CLAUDE.md              # Claude Code project description (auto-generated, @imports config files)
├── agents/                # Agent configs
│   ├── coding-agent.yaml
│   ├── planner-agent.yaml
│   ├── helper-agent.yaml
│   └── ...
├── prompts/               # Custom prompts (override built-in templates)
├── workspace/             # Agent runtime workspace
│   └── project/           # Project knowledge base (generated by nezha project init)
├── input/                 # Task input files (requirement docs, etc.)
├── .claude/               # Claude Code config (auto-generated)
│   ├── settings.json      #   Permission rules
│   └── skills/            #   16 interactive skills (EN/ZH i18n)
└── .gitignore
```

### First Run

```bash
# Simplest way: give an agent a task directly
nezha run coding-agent --prompt "Add a health check API endpoint /health"

# Or use interactive VibeCoding mode
nezha vibe coding-agent
```

---

## Usage Guide

### Core Concepts

| Concept | Description |
|---------|-------------|
| **Executor** | Global orchestrator managing scheduling, guards, and event system |
| **Agent** | A YAML-configured AI execution unit with its own model, prompt, and toolset |
| **Feature** | A requirement/deliverable, stored in `workspace/features/<id>/` |
| **Task** | A coding task in the DAG (task_list.json entry) |
| **Session** | A single LLM invocation (runs in isolated subprocess) |
| **Guard** | Pre-execution safety check (circuit breaker, balance, time window) |

### 1. Feature Queue Workflow (Recommended)

```bash
# Create Features
nezha feature create --title "User Login"
nezha feature create --title "User Registration" --input input/spec.md

# View queue
nezha feature list

# Execute (auto-picks pending Features)
nezha run coding-agent

# View Feature details
nezha feature show <feature-id>

# View dashboard
nezha dashboard --open
```

Feature state transitions:

```
pending → running → completed
                  → partial    (DAG partially completed)
                  → failed     (execution error)
```

### 2. Auto-Planning + DAG Execution

```bash
# Place requirement docs in input/ directory
cp requirements.md input/spec.md

# Create Feature (link input file)
nezha feature create --title "API v2" --input input/spec.md

# Run coding-agent — auto-invokes planner-agent to generate task_list.json
# Then executes tasks in DAG dependency order
nezha run coding-agent
```

task_list.json example:

```json
[
  {
    "id": "F-001",
    "description": "Database Schema — create user and session tables",
    "acceptance": ["Migration script executes successfully", "Tables contain required fields"],
    "depends_on": [],
    "complexity": "low",
    "passes": false
  },
  {
    "id": "F-002",
    "description": "User Registration API — POST /api/register",
    "acceptance": ["Valid request returns 201", "Duplicate email returns 409"],
    "depends_on": ["F-001"],
    "complexity": "medium",
    "passes": false
  }
]
```

### 3. Step-by-Step Approval (Feature Steps)

Set up step-by-step execution for critical Features, pausing after each step for manual approval:

```bash
# View Feature step status
nezha feature show <feature-id>

# Approve — Agent continues to next step
nezha feature approve <feature-id> <step-id>

# Reject — Agent redoes the step
nezha feature reject <feature-id> <step-id> --note "Missing error handling"
```

### 4. VibeCoding (Interactive)

Guide agents conversationally to write code, ideal for exploratory development:

```bash
nezha vibe coding-agent

# In the REPL:
> Let's look at the project structure
> Add a Redis caching layer
> Write a test to verify
> exit
```

### 5. Helper Agent (All-in-One Assistant)

One-stop control panel for analysis + operations:

```bash
# Analysis
nezha run helper-agent --prompt "Analyze current project architecture and suggest improvements"
nezha run helper-agent --prompt "What's wrong with this PR"

# Operations
nezha run helper-agent --prompt "Create a Feature: implement search functionality"
nezha run helper-agent --prompt "View all Feature statuses and generate summary report"
```

---

## Configuration Reference

### executor.yaml

```yaml
executor:
  name: "my-project"
  description: "Project description"

workspace:
  base: "./workspace"
  strategy: "per_agent"        # per_agent | shared

scheduler:
  mode: "manual"               # manual | continuous | cron
  # continuous mode:
  # interval: 60               # interval between rounds (seconds)
  # concurrency: 3             # parallel Feature execution count
  # cron mode:
  # cron: "0 2 * * *"

guards:
  - type: "circuit_breaker"
    enabled: true
    max_consecutive_errors: 3
    cooldown_seconds: 600

  - type: "balance_check"
    enabled: false
    min_balance_usd: 5.0
    max_cost_usd: 50.0         # total cost cap

  - type: "time_window"
    enabled: false
    allow: "00:00-08:00"
    timezone: "America/New_York"

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

# Target code repository (shared by all coding agents, can be overridden per-agent)
target: "/path/to/your/code-repo"

env: {}
mcp_servers: {}
```

### Agent YAML

```yaml
agent:
  name: "coding-agent"
  category: "coding"           # coding | planning | design | management
  callable: false              # true = can be called by other agents
  description: "General-purpose coding agent"

engine:
  model: "claude-sonnet-4-6"         # default fallback model
  max_turns: 100
  tools: [Read, Write, Edit, Bash, Glob, Grep]
  # Auto-select model by complexity (Planner outputs complexity, ops configures strategy):
  model_map:
    low: "claude-haiku-4-5-20251001"   # string shorthand
    medium:                             # dict full format
      model: "claude-sonnet-4-6"
    high:
      model: "claude-sonnet-4-6"
      env:                              # optional: different vendor keys
        ANTHROPIC_API_KEY: "sk-special"
  # Third-party model config:
  # env:
  #   ANTHROPIC_BASE_URL: "https://your-proxy/v1"
  #   ANTHROPIC_API_KEY: "your-key"
  security:
    allowed_commands: [ls, cat, git, python3, pytest, npm]

session:
  mode: "single_round"        # single_round | multi_round | direct
  prompts:
    worker: "coding/worker.md"
  # Or use Prompt Composer:
  # compose:
  #   worker:
  #     base: "coding/base.md"
  #     sections:
  #       - "phases/context-acquisition"
  #       - "phases/tdd"
  #       - "stacks/python"

# target is set project-wide in executor.yaml. Uncomment to override per-agent:
# target: "/path/to/override"
# target_scope: "backend"      # monorepo subdirectory

git:
  branch_per_task: true
  use_worktree: true
  base_branch: "main"
  auto_commit: true
  auto_push: false

pipeline:
  # Auto-invoke planner:
  # pre_agents:
  #   - name: "planner-agent"
  #     artifact: "task_list.json"
  # Integration testing:
  # post_task_test:
  #   enabled: true
  #   command: "pytest"
  #   max_cycles: 3
```

---

## Built-in Agents

| Agent | Type | Callable | Purpose |
|-------|------|----------|---------|
| coding-agent | coding | - | General-purpose coding, single execution |
| frontend-agent | coding | - | Frontend UI development, multi-round iteration |
| java-agent | coding | - | Java/Spring projects |
| planner-agent | planning | ✓ | Requirement docs → task_list.json |
| product-agent | planning | - | Requirement analysis → PRD |
| business-analyst-agent | planning | - | Business analysis |
| pm-agent | management | - | Project management (init project, review progress) |
| helper-agent | management | ✓ | All-in-one assistant (9 scenarios: analysis + operations) |

---

## CLI Quick Reference

```bash
# Initialize
nezha init <dir>              # Create executor workspace
nezha project init            # Initialize project knowledge base

# Run
nezha run <agent>             # Execute agent
nezha run <agent> --prompt "..." # Execute with instruction
nezha run <agent> --feature-id <id> # Execute specific Feature
nezha vibe <agent>            # Interactive VibeCoding

# Feature Management
nezha feature create --title "..." --input ...
nezha feature list            # List all Features
nezha feature list --status partial
nezha feature show <id>       # View details (incl. cost)
nezha feature approve <id> <step-id>
nezha feature reject <id> <step-id> --note "..."

# Monitoring
nezha status                  # View execution status
nezha logs -f                 # Real-time logs
nezha dashboard               # Generate HTML dashboard
nezha dashboard --open        # Generate and open in browser

# Other
nezha plan <agent>            # View execution plan
nezha integrate <id1> <id2> --repo /path --branch review
nezha rework <id> --note "..."
```

---

## Project Structure

```
nezha/
├── src/nezha/            # Core package
│   ├── __main__.py                # CLI entry point
│   ├── config.py                  # Config parsing (YAML → dataclass)
│   ├── executor.py                # Main executor
│   ├── engine.py                  # LLM engine (claude-code-sdk wrapper)
│   ├── feature_queue.py           # Feature Queue (Port/Adapter pattern)
│   ├── dag/                       # DAG engine
│   │   ├── graph.py               #   Dependency graph + dynamic state computation
│   │   ├── engine.py              #   Execution loop
│   │   ├── verifier.py            #   Two-level verification (agent self-report + external command)
│   │   └── report.py              #   Execution report generation
│   ├── pipeline/                  # Session pipeline
│   │   ├── session.py             #   Session management (subprocess isolation)
│   │   ├── prompt_composer.py     #   Prompt composer
│   │   ├── knowledge.py           #   Knowledge injection
│   │   └── security.py            #   Command allowlist
│   ├── scheduler/                 # Scheduling strategies
│   ├── guards/                    # Safety guards
│   ├── events/                    # Event system
│   ├── interface/                 # User interface
│   │   ├── cli.py                 #   CLI command implementation
│   │   └── dashboard.py           #   Dashboard generation
│   └── templates/                 # Built-in templates
│       ├── executor.yaml
│       ├── agents/                #   Agent YAML templates
│       └── prompts/               #   Prompt templates
├── agents/                        # Current project's agent configs
├── prompts/                       # Current project's custom prompts
├── workspace/                     # Runtime workspace
├── tests/                         # Test cases (933)
├── docs/                          # Architecture design docs
├── executor.yaml                  # Current project's global config
└── pyproject.toml
```

---

## Design Principles

### Workspace / Target Separation

The executor workspace (config, state, metadata) is completely separated from the target code repo:

- **workspace** — Stores feature.yaml, task_list.json, execution reports, and other metadata
- **target** — The code repository agents operate on (LLM's cwd, where git operations happen)

This allows a single executor to serve multiple code repos and prevents metadata files from polluting the code repository.

### Subprocess Isolation

Each Session runs in an isolated subprocess, with results passed via JSON files. This solves the event loop contamination issue when making consecutive claude-code-sdk calls in the same process — critical for multi_round mode to work correctly.

### Port/Adapter Pattern

Core abstractions are defined as Protocol/ABC:

- `FeatureQueue` — Currently implemented as `FileFeatureQueue` (filesystem), replaceable with Redis/MQ in the future
- `BaseGuard` — Extensible guard types
- `BaseScheduler` — Pluggable scheduling strategies
- `EventHandler` — Extensible event handlers

### Dynamic DAG State Computation

Task states (ready / blocked / completed / rework / skipped) are not persisted. Instead, `get_status()` dynamically computes them based on dependency relationships each time, preventing state inconsistencies.

---

## Advanced Features

### Parallel Execution

```yaml
# executor.yaml
scheduler:
  mode: "continuous"
  concurrency: 3               # Execute up to 3 Features simultaneously
```

Safety guarantees in parallel mode:
- Each Feature writes to an isolated `executor_status_{feature_id}.json`
- Logs are per-Feature: `{agent}_{feature_id}_{timestamp}.log`
- GuardChain uses `asyncio.Lock` to prevent concurrent race conditions
- Cost tracker aggregates across Features, supports `max_cost_usd` global budget

### Third-Party Models

Compatible with any Anthropic API-compatible proxy via environment variables:

```yaml
# agents/coding-agent.yaml
engine:
  model: "glm-5"
  env:
    ANTHROPIC_BASE_URL: "https://open.bigmodel.cn/api/anthropic"
    ANTHROPIC_API_KEY: "your-key"
```

### model_map — Complexity-Based Model Mapping

Planner Agent labels tasks with `complexity` (low/medium/high), and the coding Agent auto-selects the corresponding model via `model_map`. Ops can change strategies anytime without modifying task_list.json.

**Three-level resolution priority**: `task.model` (explicit) > `model_map[complexity]` > `engine.model` (default)

```yaml
# agents/coding-agent.yaml
engine:
  model: "claude-sonnet-4-6"        # default fallback
  model_map:
    low: "claude-haiku-4-5-20251001"  # simple tasks use Haiku (cost-saving)
    medium:
      model: "claude-sonnet-4-6"
    high:
      model: "claude-sonnet-4-6"
      env:                             # optional: different API for high complexity
        ANTHROPIC_API_KEY: "sk-premium"
```

**Common strategies**:

```yaml
# Cost-saving mode — all Haiku
model_map:
  low: "claude-haiku-4-5-20251001"
  medium: "claude-haiku-4-5-20251001"
  high: "claude-haiku-4-5-20251001"

# Speed mode — all Sonnet
model_map: {}   # empty = all use engine.model default

# Multi-vendor mode
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

### Prompt Composer

Modular prompt composition to avoid excessive duplication:

```yaml
session:
  compose:
    worker:
      base: "coding/base.md"          # Role declaration
      sections:
        - "phases/context-acquisition" # Context acquisition phase
        - "phases/tdd"                 # TDD workflow
        - "phases/commit-rules"        # Commit conventions
        - "stacks/python"             # Python tech stack
        - "concerns/quality-tracking"  # Quality tracking
```

Modules in three categories:
- **phases/** — Workflow stages (context-acquisition, rework, tdd, regression, commit-rules)
- **stacks/** — Tech stack knowledge (java-spring, python, frontend, general)
- **concerns/** — Cross-cutting concerns (exec-plan, quality-tracking)

### MCP Server Integration

```yaml
# executor.yaml (global) or agent YAML (per-agent)
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

### Integration Test Auto-Fix

```yaml
# agents/coding-agent.yaml
pipeline:
  post_task_test:
    enabled: true
    command: "pytest"
    max_cycles: 3              # Auto-fix up to 3 rounds
    timeout: 600
```

After all DAG tasks complete, integration tests run automatically. On failure, a fix Session is launched, looping until tests pass or the limit is reached.

---

## Testing

```bash
# Run all tests
make test

# Quick check
make test-quick

# Run a single module
make test-file F=test_parallel_safety.py

# Or use pytest directly
python -m pytest tests/ -v
```

Currently **933** tests covering core execution flow, DAG engine, Feature Queue, guard system, event system, Prompt Composer, parallel safety, model mapping, and more.

---

## Typical Workflow

```
                 ┌──────────────┐
                 │ User submits │
                 │  requirement │
                 └─────┬────────┘
                       │
          nezha feature create --title "..." --input spec.md
                       │
                       ▼
              ┌────────────────┐
              │ planner-agent  │  Auto-generates task_list.json
              │  (auto invoke) │
              └───────┬────────┘
                      │
                      ▼
              ┌────────────────┐
              │ coding-agent   │  Executes in DAG dependency order
              │  (multi_round) │
              └───────┬────────┘
                      │
               ┌──────┴──────┐
               │ Integration │
               │ tests pass? │
               └──────┬──────┘
                 Yes  │   No → Auto-fix Session
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
              │  Manual Review │
              └────────────────┘
```

---

## Roadmap

### V2.0 — Core Capability Enhancement ✅

- [x] **F1** Chained Branches — `--base-branch` supports chained branch creation
- [x] **F2** per-Task Model — `"model"` field override in task_list.json
- [x] **F3** Integration Verification — DAGEngine integration_prompt_path + integration Session
- [x] **F4** Feature Steps — Step-by-step execution + approve/reject approval
- [x] **F5** Cost Tracking — execution-report.md parsing + cost summary

### V2.1 — UX Optimization ✅

- [x] **F1** Helper All-in-One — 9-scenario unified control panel (analysis + operations)
- [x] **F2** Dashboard — Static HTML visualization dashboard
- [x] **F3** Parallel Execution — asyncio.Semaphore concurrency control + safety isolation

### V2.2 — Multi-Model Strategy ✅

- [x] **F1** model_map — complexity → model + env three-level resolution, decoupling Planner from model selection
- [x] **F2** Global User Config — `~/.nezha/config.yaml` auto-merge locale/timezone/env/model_map
- [x] **F3** task_factor — ModelMapEntry.task_factor + planner granularity adaptation (weaker models split finer, stronger models split coarser)

### V2.3 — Claude Code Native Integration ✅

- [x] **F1** Claude Code Integration — `nezha init` auto-generates CLAUDE.md + `.claude/skills/` + `settings.json`, 16 interactive skills (EN/ZH i18n)
- [x] **F2** AI Judge Multi-Model — `failure_strategy: "ai_judge"` supports Anthropic + OpenAI-compatible API (GLM/Kimi/MiniMax)
- [x] **F3** slugify ASCII-only — Feature ID / branch contain only ASCII characters, Chinese titles preserved in title field for display

### Future Directions

- [ ] Web UI — Graphical management interface to replace CLI
- [ ] Distributed Multi-Machine — Redis/MQ backend, cross-machine Agent scheduling
- [ ] Webhook Events — Push execution events to external systems
- [ ] Agent Marketplace — Shareable Agent configuration templates

---

## Contributing

Issues and Pull Requests are welcome.

1. Fork this repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push the branch (`git push origin feature/amazing-feature`)
5. Create a Pull Request

Please ensure all tests pass:

```bash
make test-quick
```

---

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.
