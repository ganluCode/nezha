## 你的任务 - 生成功能列表

当前工作空间：{{workspace}}
项目：{{project_name}}

{{input_files}}

### 任务

读取输入需求文档，产出一份将项目拆解为小型、可独立实现功能的 **{{workspace}}/task_list.json**。

### 重要提示

- 你**必须**使用 Write 工具创建 `{{workspace}}/task_list.json` 文件
- 文件路径：`{{workspace}}/task_list.json`
- 完成后简单报告生成的功能数量即可

### 输出格式

在 `{{workspace}}/task_list.json` 写入以下格式的 JSON：

```json
[
  {
    "id": "F-001",
    "description": "简洁但具体地描述要实现的内容",
    "acceptance": ["验收标准1", "验收标准2"],
    "depends_on": [],
    "passes": false
  }
]
```

### 功能设计规则

1. **粒度**：每个功能应在一次编码 session 内可完成（约 30-60 分钟）
2. **独立性**：最小化依赖，基础功能优先
3. **可测性**：每个验收标准必须可客观验证
4. **DAG 合法性**：`depends_on` 必须构成合法 DAG（无环）
5. **ID 格式**：顺序编号：F-001、F-002、F-003……
6. **全部 passes: false**：不要将 passes 设为 true

### 验收标准指南

- 具体：「API 返回 200，JSON 体包含 'id' 字段」而非「API 可用」
- 可测：「用无效密码登录返回 401」而非「安全性好」
- 包含边界：「空输入返回 400 并带错误信息」
