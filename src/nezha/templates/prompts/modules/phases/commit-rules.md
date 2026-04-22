### RULES

- Work on the **assigned target feature only** — the executor manages execution order
- **Do NOT switch branches** — the executor has already placed you on the correct branch. Never run `git checkout`, `git switch`, or `git branch` to change branches. Commit directly on the current branch.
- Follow the existing code naming and style conventions
- Do NOT modify build/dependency files unless the feature explicitly requires a new dependency
- Always run tests before committing
- Leave the workspace in a clean, working state
- Use `git diff` and `git status` to verify changes before committing
- Do NOT delete entries or modify the structure of {{workspace}}/task_list.json
- Allowed field changes in {{workspace}}/task_list.json: `passes`, `rework`, `rework_note`, `rework_count`
- If `rework_count >= 3`, document the blocker in {{workspace}}/progress.md and move on
