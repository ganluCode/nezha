### AFTER IMPLEMENTATION — REGRESSION CHECK

1. Run the full test suite: `{{test_command}}`
2. If a previously passing test now fails:
   - Set that feature's `passes` to `false`, add `rework: true`
   - Add `rework_note` with `"block_reason": "Regression: <test name> — <error>"`
3. Update `{{workspace}}/progress.md`
