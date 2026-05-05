### 项目规范 — 前端

实现任何代码前，先阅读这些文件了解项目：
- `package.json` — 依赖、scripts、框架版本
- `tech_stack.yaml`（如有）— UI 库、CSS 方案、组件规范
- `src/components/` 和 `src/pages/` 中的现有源代码结构

测试命令：`npm test` 或 `npx vitest run`

**测试方法（Testing Library）**：
- 用 `getByRole`、`getByText`、`getByLabelText` 查询元素（用户/无障碍视角）
- 用 `userEvent`（不是 `fireEvent`）模拟交互
- 用 MSW mock API 请求（在网络层拦截，而不是 mock `fetch`）
- 优先写集成测试（页面/feature 级别），少写单组件单元测试
- 自定义 hook 仅在包含业务逻辑时用 `renderHook` 测试

**前端特定测试模式**：
- 用户交互流程（"用户点按钮 → 看到结果"）
- 条件渲染（"数据为空 → 显示空状态"）
- 表单校验（"非法邮箱 → 显示错误信息"）
- 异步操作结果（"加载完成 → 列表显示 3 项"）
- 错误边界（"API 失败 → 显示错误信息"）

**不要测试**：
- 组件内部的状态变量
- CSS class 名或 DOM 结构
- 父子组件之间的 props 传递
- 第三方库行为（React/Vue 渲染、router 内部）
- 具体的像素值或样式细节

### 前端最佳实践

- **响应式**：确保 UI 适配 mobile/tablet/desktop 视口
- **无障碍**：添加基础 ARIA 属性（`aria-label`、`role`）并支持键盘导航
- **可复用**：把通用 UI 元素抽到 `src/components/common/` 中复用
- **样式隔离**：一致使用 CSS Modules、Tailwind 或项目的 CSS 方案，避免污染
- **代码整洁**：提交前移除 `console.log` 语句和未使用的 import
- **构建检查**：运行 `npm run build` 或 `npm run lint`，修复 TypeScript 错误或 ESLint 警告
- **脚手架**：在 `src/components/` 或 `src/pages/` 创建新文件时遵循现有目录结构
- **集成**：把新组件 import 并集成到父容器或路由配置中
- **遵守技术栈**：严格遵循项目定义的 UI 库和样式方案
