### 返工流程（如果 `is_rework` 为 TRUE）

1. 读取 `rework_note`：`block_reason`、`tried`、`not_tried`、`related_files`、`attempt`
2. 检查 `state/traces/` 中的历史执行记录
3. 用**与 `tried` 中不同的方案**修复
4. 运行测试：`{{test_command}}`
5. 修复成功：将 {{workspace}}/task_list.json 的 `passes` 设为 `true`，移除 `rework` 和 `rework_note`
6. 仍然失败：将 `rework_note` 更新为 JSON 对象：
   ```json
   {
     "attempt": <上次值 + 1>,
     "tried": "<本次尝试的内容，追加到之前的 tried>",
     "not_tried": "<尚未尝试的备选方案>",
     "related_files": ["<查看或修改过的文件>"],
     "block_reason": "<已知的根因>"
   }
   ```
7. 提交：`git add -A && git commit -m "<feature-id>: rework - <简短描述>"`
