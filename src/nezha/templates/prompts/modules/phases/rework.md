### REWORK PATH (If `is_rework` is TRUE)

1. Read `rework_note`: `block_reason`, `tried`, `not_tried`, `related_files`, `attempt`
2. Check `state/traces/` for previous execution history
3. Fix using a **different approach** from what's in `tried`
4. Run tests: `{{test_command}}`
5. If fixed: set `passes: true`, remove `rework` and `rework_note` from {{workspace}}/task_list.json
6. If still failing: update `rework_note` as JSON:
   ```json
   {
     "attempt": <previous + 1>,
     "tried": "<what you tried, appended to previous>",
     "not_tried": "<alternatives not yet attempted>",
     "related_files": ["<files examined or modified>"],
     "block_reason": "<root cause if known>"
   }
   ```
7. Commit: `git add -A && git commit -m "<feature-id>: rework - <brief description>"`
