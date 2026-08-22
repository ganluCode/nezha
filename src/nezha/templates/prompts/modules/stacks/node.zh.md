### 项目规范 — NODE.JS

实现任何代码前，先阅读这些文件了解项目：
- `package.json` — scripts、模块类型、包管理器、依赖
- `package-lock.json` / `pnpm-lock.yaml` / `yarn.lock` — 包管理器和锁定版本
- `tsconfig.json` — TypeScript target、module 模式、strict 配置
- 现有 `src/` 和 `test/` 结构 — 命名、导出方式、分层、测试风格
- `.eslintrc*`、`eslint.config.*`、`.prettierrc*` — lint 和格式化规则

优先使用项目已有 scripts，不要随手拼临时命令：
- 测试：`npm test`、`npm run test`、`pnpm test` 或 `yarn test`
- 构建/类型检查：`npm run build`、`npm run typecheck` 或 `npx tsc --noEmit`
- Lint：`npm run lint`

**典型 Node.js 项目结构**：

```
src/
  index.js|ts          <- 对外入口
  cli.js|ts            <- CLI 入口（如有）
  services/            <- 业务逻辑
  repositories/        <- 持久化和外部数据访问
  routes/              <- HTTP routes/controllers
  utils/               <- 小型可复用工具
test/
  *.test.js|ts         <- 单元/集成测试
```

### NODE.JS 测试实践

- 使用项目现有测试框架。除非明确要求，不要迁移 Jest/Vitest/node:test。
- 如果项目使用 Node 内置测试框架，使用 `node:test` 和 `node:assert/strict`。
- 通过公共导出、CLI 命令或 HTTP handler 测试可观察行为。
- 覆盖错误场景、非法输入和异步 reject 路径。
- 测试要稳定可重复：不要真实访问网络、不要依赖 sleep、不要共享全局状态。
- 每个测试之间重置内存 store 或 mock。

### NODE.JS 最佳实践

- 业务逻辑不要写在 CLI/HTTP adapter 里，委托给 service。
- 可复用函数优先使用明确的 named exports，除非项目已有 default export 约定。
- 保持现有模块系统不变（`type: "module"` ESM 或 CommonJS）。
- 除非任务要求且项目接受依赖变更，不要新增依赖。
- 显式处理异步错误，避免 unhandled promise。
- 在边界层校验外部输入，并返回清晰错误。
- 库/服务代码不要留下调试用 `console.log`。
- 修改后运行相关测试，验证通过后再更新 `task_list.json`。
