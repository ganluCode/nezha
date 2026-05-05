### 执行计划

`{{workspace}}/exec-plan.md` 文件包含当前的执行进度表。每个 session 开始时阅读它，了解已完成和待完成的内容。

完成任务后：
- DAG 引擎会在下次 session 开始时自动更新 `{{workspace}}/exec-plan.md`
- 你**不需要**手动更新 `{{workspace}}/exec-plan.md`
- 应该更新的是 `{{workspace}}/task_list.json` 的字段：`passes`、`rework`、`rework_note`、`rework_count`
