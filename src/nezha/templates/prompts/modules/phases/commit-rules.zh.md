### 规则

- 只处理**分配的目标 feature** — 执行顺序由执行器管理
- **禁止切换分支** — 执行器已将你置于正确的分支上。不要运行 `git checkout`、`git switch` 或 `git branch` 来切换分支。直接在当前分支上提交。
- 遵循现有的代码命名和风格规范
- 不要修改构建/依赖文件，除非 feature 明确要求新增依赖
- 提交前必须运行测试
- 保持工作空间处于干净、可运行的状态
- 提交前用 `git diff` 和 `git status` 确认变更内容
- 不要删除条目或修改 {{workspace}}/task_list.json 的结构
- {{workspace}}/task_list.json 允许修改的字段：`passes`、`rework`、`rework_note`、`rework_count`
- 若 `rework_count >= 3`，在 {{workspace}}/progress.md 中记录阻塞原因并跳过
