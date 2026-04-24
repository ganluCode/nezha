### 项目规范 — TAURI 全栈（Rust + Python Sidecar + 前端）

你正在开发一个 **Tauri 2.0 + Python Sidecar + React/Vue** 桌面 app。
单个 feature 通常**横跨 3 种语言** — 需要协调实现所有部分。

#### 架构

```
前端 (React/Vue)        ← UI + 用户交互
   ↓ invoke('xxx')
Tauri Rust              ← 极薄的 IPC handler，转发请求
   ↓ JSON-RPC over stdin/stdout
Python Sidecar          ← 所有业务逻辑、AI 调用、数据处理
```

#### 标准目录布局

```
src-tauri/                   ← Tauri Rust（薄壳）
├── src/
│   ├── main.rs              ← 入口
│   ├── commands/            ← IPC handlers（一个 feature 一个文件）
│   ├── sidecar/             ← Sidecar 进程管理
│   └── ipc.rs               ← JSON-RPC 协议
├── tauri.conf.json
└── Cargo.toml

sidecars/<sidecar-name>/     ← Python 业务后端
├── src/
│   ├── main.py              ← JSON-RPC server 入口
│   ├── rpc/                 ← 协议层
│   └── handlers/            ← 业务 handlers（一个 feature 一个文件）
├── tests/
└── pyproject.toml

src/                         ← 前端
├── api/                     ← invoke 封装
├── pages/                   ← 页面
├── components/              ← 通用组件
├── stores/                  ← Pinia / Zustand
└── types/                   ← TS 类型（与 Rust/Python 共享）

shared/                      ← 跨语言共享（如有）
└── schema/                  ← JSON schema 定义
```

实现前先读项目实际结构（可能略有不同），不要假设。

#### 新增 feature — 必须修改的文件（跨语言）

「新增 X 功能」类任务通常需要修改**所有 3 层**：

| 层级 | 文件 | 职责 |
|------|------|------|
| 前端 | `src/api/<feature>.ts` | invoke 封装 |
| 前端 | `src/pages/<Feature>.vue`（或 .tsx） | UI |
| Tauri Rust | `src-tauri/src/commands/<feature>.rs` | IPC handler（5-15 行） |
| Tauri Rust | `src-tauri/src/main.rs` 注册 command | 一行 |
| Python | `sidecars/<sidecar>/src/handlers/<feature>.py` | **业务逻辑核心** |
| Python | `sidecars/<sidecar>/src/main.py` 注册 RPC method | 一行 |
| 测试 | `sidecars/<sidecar>/tests/test_<feature>.py` | Python 单测 |

#### 实现顺序（TDD）

新 feature 按这个顺序实现：
1. **定义类型** 在 `src/types/<feature>.ts`（单一真相源）
2. **Python handler** + 单测（RED → GREEN）
3. **Tauri Rust command**（薄薄一层）
4. **前端 API wrapper**（`src/api/<feature>.ts`）
5. **前端 UI**（page 或 component）
6. **手工烟雾测试**（`pnpm tauri dev` 跑一遍）

#### JSON-RPC 协议（Rust ↔ Python）

```
Request:  {"jsonrpc": "2.0", "id": <int>, "method": "feature_list", "params": {...}}
Response: {"jsonrpc": "2.0", "id": <int>, "result": ...}
Error:    {"jsonrpc": "2.0", "id": <int>, "error": {"code": -1, "message": "..."}}
```

- Method 命名：**snake_case**，如 `feature_list`、`phase_show`
- 每个 method 必须在 `handlers/` 里注册到 dispatcher

#### 跨 3 种语言的类型契约

**TypeScript**（前端，camelCase）：
```ts
interface Feature {
  id: string;
  title: string;
  createdAt: string;
}
```

**Rust**（用 serde 自动转换）：
```rust
#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct Feature {
    id: String,
    title: String,
    created_at: String,
}
```

**Python**（用 pydantic 校验）：
```python
from pydantic import BaseModel, Field

class Feature(BaseModel):
    id: str
    title: str
    created_at: str = Field(alias="createdAt")
```

#### Tauri Rust — Command 模式

```rust
// src-tauri/src/commands/feature.rs
use serde_json::json;

#[tauri::command]
pub async fn feature_list(
    state: tauri::State<'_, AppState>
) -> Result<Vec<Feature>, String> {
    state.sidecar
        .call("feature_list", json!({}))
        .await
        .map_err(|e| e.to_string())
}
```

```rust
// src-tauri/src/main.rs（注册）
.invoke_handler(tauri::generate_handler![
    commands::feature::feature_list,
])
```

**规则**：
- Command 函数应该是 5-15 行；超过说明业务逻辑漏到 Rust 了
- 用 `Result<T, String>` 让前端 try/catch
- 不要 `.unwrap()` / `.expect()`，用 `?` 传播错误

#### Python Sidecar — Handler 模式

```python
# sidecars/<name>/src/handlers/feature.py
from typing import Any
from pydantic import BaseModel

class FeatureListParams(BaseModel):
    pass  # 无参数

async def feature_list(params: dict[str, Any]) -> list[dict]:
    """业务逻辑核心实现"""
    # 调用任何 Python 库（nezha, langchain, etc.）
    return [{"id": "f1", "title": "Feature 1"}]
```

```python
# sidecars/<name>/src/main.py（注册）
from handlers import feature

HANDLERS = {
    "feature_list": feature.feature_list,
}
```

#### 前端 — API Wrapper

```typescript
// src/api/feature.ts
import { invoke } from '@tauri-apps/api/core';
import type { Feature } from '../types/feature';

export async function featureList(): Promise<Feature[]> {
  return await invoke<Feature[]>('feature_list');
}
```

```vue
<!-- src/pages/FeatureList.vue -->
<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { featureList } from '@/api/feature';

const features = ref([]);
onMounted(async () => {
  features.value = await featureList();
});
</script>
```

#### 构建 & 测试命令

```bash
# 开发模式（前端 + Tauri + sidecar 自动重启）
pnpm tauri dev

# 完整打包
pnpm tauri build

# 单独跑测试
cd sidecars/<sidecar-name> && pytest -v   # Python
cd src-tauri && cargo test                 # Rust
pnpm test                                  # 前端

# 类型检查
pnpm tsc --noEmit                          # TS
cd src-tauri && cargo check                # Rust
cd sidecars/<sidecar-name> && mypy src     # Python（如配置）
```

#### 常见反模式（不要做）

- ❌ **不要在 Rust 里写业务逻辑** — Rust 只负责转发 IPC，业务全在 Python
- ❌ **不要在 React/Vue 里直接调 Python** — 必须经过 `invoke()`
- ❌ **不要让前端直连 sidecar HTTP 端口** — sidecar 只接 stdin/stdout
- ❌ **不要在 sidecar 里做 UI 相关的事**（弹窗、通知等）— 通过 IPC 让 Tauri 做
- ❌ **不要在 Rust 里 spawn 长时间任务的 thread** — 用 `tokio::spawn`
- ❌ **不要硬编码路径** — 用 `tauri::api::path::*`（Rust）或 `appDir()`（前端）
- ❌ **不要在 sidecar 关闭时丢失正在跑的任务** — 处理 SIGTERM 优雅退出

#### 推荐做法（要做）

- ✅ **类型定义只写一遍**（TS），Rust/Python 用代码生成或手工对齐
- ✅ **每个 RPC method 都要 Python 单测覆盖**
- ✅ **Tauri command 错误用 String 包装**，前端能 try/catch
- ✅ **长任务用进度回调** — Python 通过事件流推送，Rust 转发，前端订阅
- ✅ **Sidecar 崩溃要能自动重启** — Tauri 端实现 supervisor 模式
- ✅ **前端 mock 数据用 MSW** 或对应方案，不要直接 mock `invoke`
- ✅ **跨语言改动放在同一个 commit** — 保持 IPC 契约的原子性

#### 跨层测试策略

| 测试类型 | 位置 | 用途 |
|---------|------|------|
| Python 单测 | `sidecars/*/tests/` | 业务逻辑（最重要） |
| Rust 单测 | `src-tauri/src/**/*.rs` `#[cfg(test)]` | IPC 协议、状态管理 |
| 前端单测 | `src/**/*.test.ts` | UI 组件、hook |
| 集成测试 | `e2e/`（可选） | 完整流程，启动 app 跑 |

测试金字塔：Python 单测最多，前端组件测中等，E2E 少而精。

#### 性能注意

- Sidecar 启动慢（Python 起进程 + import）— 在 splash 屏幕时启动
- IPC 序列化有开销 — 大数据（>1MB）用流式或文件传递
- React/Vue 渲染要用虚拟列表 — DAG 节点多时
