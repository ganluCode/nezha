# 安装与初始化

3 分钟把 Nezha 装好，把第一个 Harness 工程跑起来。

## 前置依赖

| 依赖 | 用途 | 怎么装 |
|------|------|--------|
| Python ≥ 3.10 | Nezha 运行环境 | `brew install python@3.13` |
| pipx | 隔离安装 CLI 工具 | `brew install pipx && pipx ensurepath` |
| Claude Code CLI | 真正调用 LLM 的底层工具 | `npm install -g @anthropic-ai/claude-code` |
| Git | 代码仓库管理 | macOS 自带 |
| `gh`（可选） | GitHub PR 创建 | `brew install gh && gh auth login` |

> 不用 macOS 的话，把 `brew install` 换成对应包管理器即可。

## 安装 Nezha

> 当前 Nezha 还没发布到 PyPI，需要从源码安装。

```bash
git clone https://github.com/ganluCode/nezha.git
cd nezha
pipx install .
```

国内网络如果 pip 源 403，加官方源参数：

```bash
PIP_INDEX_URL=https://pypi.org/simple pipx install .
```

安装完成后验证：

```bash
nezha --version
nezha --help
```

看到子命令列表就成功了：

```
usage: nezha [-h] [-v]
  {run,status,history,logs,rework,vibe,plan,feature,task,phase,
   init,code,integrate,project,agent-context,dashboard,
   pause,resume,stop,heartbeat} ...
```

## 第一次初始化：创建 Harness 工程

回顾一下 [上一节](01-concepts.md#harness-工程-vs-target-仓库) 的关键概念：**Harness 工程不是代码仓库**，是 Nezha 用来管理流程的工程目录。

我们要做一个简历生成器，先建一个 Harness 工程：

```bash
nezha init my-resume-harness
```

执行后 Nezha 会生成完整的工程结构：

```
my-resume-harness/
├── executor.yaml              ← 全局配置（项目名、scheduler、model_map）
├── .env.example               ← 环境变量模板
├── agents/                    ← 各种 Agent 配置
│   ├── planner-agent.yaml
│   ├── frontend-agent.yaml
│   ├── python-agent.yaml
│   └── ...
├── prompts/                   ← Prompt 模板
│   ├── modules/
│   └── ...
├── workspace/                 ← Phase/Feature 状态目录（运行后自动产生）
├── state/                     ← 日志和事件目录（运行后自动产生）
├── CLAUDE.md                  ← 给 Claude Code 用的项目说明
└── .claude/                   ← Claude Code 集成配置
    ├── settings.json
    └── skills/                ← 17 个内置 skill
```

## 准备环境变量

复制 `.env.example` 为 `.env`：

```bash
cd my-resume-harness
cp .env.example .env
```

打开 `.env` 文件，根据需要填入：

```bash
# ============= GitHub 相关 =============
# 用于 nezha run 时自动 create-pr
# 取自 https://github.com/settings/tokens
GH_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx

# ============= 模型相关 =============
# 如果你用 Claude Code 登录态，可以不配 ANTHROPIC_API_KEY
# 如果你要用三方模型（GLM/Kimi 等），在 executor.yaml 的 model_map 配
```

> **重要**：`.env` 不要 commit 到 git，模板里已经默认 `.gitignore` 了。

## Harness 工程要不要 git 跟踪

**可以也可以不**。看你的偏好：

| 场景 | 建议 |
|------|------|
| 个人小项目，临时跑跑 | 不用 git 跟踪 |
| 团队协作，多人共享 Harness 配置 | git 跟踪（但 `.env` 和 `state/`、`workspace/` 加 `.gitignore`） |
| 想保留 Phase/Feature 的演进历史 | git 跟踪 |

如果要 git 跟踪：

```bash
cd my-resume-harness
git init
git add executor.yaml agents/ prompts/ CLAUDE.md .claude/
git commit -m "init nezha harness"
```

## 验证安装

```bash
nezha status
```

应该看到类似输出：

```
==================================================
Nezha Executor Status
==================================================
Status: idle
Current agent: -
Session ID: 0
Started at: -
```

没报错 = 安装成功。

## 常见问题

**Q: 装完 `nezha` 命令找不到？**

```bash
pipx ensurepath
source ~/.zshrc  # 或 ~/.bashrc
```

**Q: 清华 pip 源 403？**

加官方源参数绕过：

```bash
PIP_INDEX_URL=https://pypi.org/simple pipx install --force .
```

或永久切换到阿里源：

```bash
pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/
```

**Q: 修改了源码，想重新安装？**

```bash
cd /path/to/nezha
pipx install --force .
```

`--force` 会覆盖已安装版本。

---

Harness 工程已经就绪。下一步去 [03 - 配置 executor.yaml](03-configure.md)。
