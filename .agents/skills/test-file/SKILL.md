---
name: test-file
description: 运行单个测试文件，用法：/test-file test_config.py
---

运行指定测试文件：

!`python3 -m pytest tests/$ARGUMENTS -v --tb=short 2>&1 | tail -50`

如果有失败，分析原因并给出修复建议。
