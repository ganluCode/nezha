### 实现后 — 回归检查

1. 运行完整测试套件：`{{test_command}}`
2. 如果之前通过的测试现在失败：
   - 将该 feature 的 `passes` 设为 `false`，添加 `rework: true`
   - 添加 `rework_note`：`"block_reason": "Regression: <测试名> — <错误>"`
3. 更新 `{{workspace}}/progress.md`
