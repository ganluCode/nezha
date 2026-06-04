---
name: test
description: 运行全量测试，快速验证无回归
---

运行完整测试套件并汇报结果：

!`python3 -m pytest tests/ -q --tb=short 2>&1 | tail -30`

如果有失败，分析失败原因并给出修复建议。测试全部通过则简短确认。
